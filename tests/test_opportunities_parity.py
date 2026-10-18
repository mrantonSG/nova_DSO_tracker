"""The browser's Imaging Opportunities must be the route's.

static/js/nova_opportunities.js (NovaOpportunities.forObject) rebuilds the "results"
of core.get_imaging_opportunities() for one object, so the graph view's table can be
filled without the server. This pins the two together: a few catalogue objects and,
per site, two synthetic ones that culminate a hair above the site's altitude
threshold and above min_max_altitude, at London, Singapore, Tromso (with its own
threshold), Perth and London behind a horizon mask, under the template's imaging
criteria and a loose set (min_observable_minutes 0, so 'N/A' from/to appear).

Instants: every site on 2026-10-18 12:00 UTC (London's nights include the 25 Oct
clock change), Singapore at 00:30 local (the first night is the calendar date, not
the observing night), London on 2026-03-20 (the 28 Mar clock change, where the route
prints the morning end in GMT) and Tromso on 2026-09-05 (twelve nights without
astronomical dusk, then with). The template's search_horizon_months is cut to
HORIZON_MONTHS to keep the file near 30 s; the loose set covers a whole lunar month.

Server: the route itself through the test client with plot_loc_name, as the graph
page calls it, datetime.now() frozen in nova.blueprints.core. Recorded underneath
(real astropy and ephem): each night's observable minutes and true maximum altitude,
and the raw separation (BaseCoordinateFrame.separation wrapped). The raw Moon is
ephem's at the route's instant, recomputed here with the route's expression.
Browser: /api/desktop_inputs?location=... from the same client, fed to forObject()
with the same instant; NovaAstro is wrapped to record the same raw values per night,
the sun events, and how close a sample comes to the threshold or the mask.

Must match exactly: status, the row keys, the dates and their order, and every field
of rows on both sides (numbers compared as numbers: JSON 40.0 and 40 are the same
value to the page). Anything else is one of these classes, counted per case and
capped; anything unclassified fails:
  - moon: moon_illumination one 0.1 step where the route's unrounded phase is within
    EPS_PHASE_EDGE of a halfway point; a row on one side only where it is within
    MOON_BAND of max_moon_illumination (the browser filters on one decimal).
  - separation: moon_separation one 0.1 step where the route's raw separation is
    within EPS_SEP of a halfway point; a row on one side only where it is within
    EPS_SEP of min_angular_separation.
  - night edge: the night's twilight strings differ (a string the window is built
    from, the browser's instant within EPS_TWILIGHT_S of the minute, or the two
    strings exactly one minute apart: where the Sun crosses -18 deg slowly, at high
    latitude near the end of the white nights, a hair of altitude moves the crossing
    by tens of seconds), so the sample grid moves by a minute and may gain or lose
    its last sample: from/to by at most one sample plus a minute, obs_minutes by one sample, max_alt by what an altitude
    can change in one sample, separation by SEP_PER_MINUTE more, rating by one star
    across a boundary; a row on one side only where those moves straddle a filter.
  - threshold edge: obs_minutes by one sample (from/to by one sample, or 'N/A'
    against a time) where one of the browser's samples lies within EPS_DEG of the
    threshold or the mask; max_alt one 0.1 step where the raw maximum is within
    EPS_DEG of a halfway point; a row on one side only where a raw maximum is within
    EPS_DEG of min_max_altitude or the minutes straddle min_observable_minutes that way.
  - rating: one star, only where the two raw scores lie on either side of a star
    boundary within SCORE_TOL of each other.
  - grazing night: one side has an astronomical dusk or dawn and the other 'N/A', and
    astropy's lowest Sun that night is within EPS_GRAZING_DEG of -18 deg — the engines
    disagree whether the Sun dips below -18 for a few minutes (Tromso, 16 Sep), so
    the window, the dusk instant and everything built on them differ: any difference
    in that night's rows.
The Moon, a night edge and a grazing night belong to the night, so they are capped
in nights; the rest in rows.
"""

import functools
import json
import math
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path

import astropy.units as u
import ephem
import numpy as np
import pytest
import pytz
import yaml
from astropy.coordinates import AltAz, BaseCoordinateFrame, EarthLocation, get_body
from astropy.time import Time

import nova.blueprints.core as core
from nova.config import astro_context_cache
from nova.models import AstroObject, DbUser, HorizonPoint, Location, UiPref
from modules.astro_calculations import calculate_sun_events_cached

REPO = Path(__file__).resolve().parent.parent
STATIC_JS = REPO / "static" / "js"
CONFIG_DEFAULT = REPO / "config_templates" / "config_default.yaml"
NODE = shutil.which("node")
ENGINE_FILES = ("nova_astro.js", "nova_dashboard_rows.js", "nova_outlook.js", "nova_opportunities.js")

MASK = [[0.0, 15.0], [45.0, 30.0], [100.0, 24.0], [160.0, 12.0], [200.0, 28.0], [250.0, 35.0], [320.0, 22.0]]
# (name, lat, lon, tz, own altitude threshold or None, horizon mask)
SITES = [
    ("London", 51.509865, -0.118092, "Europe/London", None, []),
    ("Singapore", 1.3521, 103.8198, "Asia/Singapore", None, []),
    ("Tromso", 69.6492, 18.9553, "Europe/Oslo", 25.0, []),
    ("Perth", -31.9522, 115.8614, "Australia/Perth", None, []),
    ("London masked", 51.509865, -0.118092, "Europe/London", None, MASK),
]
GLOBAL_THRESHOLD = 20
BASE = datetime(2026, 10, 18, 12, 0, tzinfo=dt_timezone.utc)
CASES = [(site[0], BASE) for site in SITES] + [
    ("Singapore", datetime(2026, 10, 18, 16, 30, tzinfo=dt_timezone.utc)),   # 00:30 on the 19th
    ("London", datetime(2026, 3, 20, 12, 0, tzinfo=dt_timezone.utc)),        # 28 Mar clock change
    ("Tromso", datetime(2026, 9, 5, 12, 0, tzinfo=dt_timezone.utc)),         # dusk returns on 17 Sep
]

# Catalogue objects from test_outlook_parity: rows at two to four sites, two
# circumpolar ones, one on a night edge (IC1318) and one on a threshold edge (C33).
OBJECTS = ("NGC7822", "NGC2264", "M65", "M101", "M104", "C57", "C109", "IC1318", "C33")
EDGE_RA = 1.6            # on the meridian near local midnight in mid-October
EDGE_ABOVE = 0.0005      # degrees above the line the synthetic object culminates
HORIZON_MONTHS = 2
LOOSE_CRITERIA = {"min_observable_minutes": 0, "min_max_altitude": 0, "max_moon_illumination": 100,
                  "min_angular_separation": 0, "search_horizon_months": 1}

EPS_TWILIGHT_S = 5.0
EPS_DEG = 0.002
EPS_PHASE_EDGE = 0.02    # the engines' phases differ by up to 0.0115 here (London, 24 Mar)
MOON_BAND = 0.05
EPS_SEP = 0.02
SEP_PER_MINUTE = 0.01    # the Moon moves about 0.55 deg an hour
SCORE_TOL = 0.05
EPS_GRAZING_DEG = 0.01
STAR_BOUNDARIES = (12.5, 37.5, 62.5, 87.5)
CLASSES = ("moon", "separation", "night edge", "threshold edge", "rating", "grazing night")
NIGHT_CLASSES = ("moon", "night edge", "grazing night")
# Per case (one criteria set, one site, one instant, every object); NIGHT_CLASSES in
# nights, the rest in rows.
CAPS = {"moon": 10, "night edge": 8, "separation": 30, "threshold edge": 6, "rating": 4, "grazing night": 1}
ROW_KEYS = {"date", "obs_minutes", "from_time", "to_time", "max_alt", "moon_illumination",
            "moon_separation", "rating"}

# Loads the four files the way the page does and runs every (case, object). NovaAstro
# is a copy of itself whose sunEvents, moonPhaseAt, observableDuration and
# moonSeparation record what they return, per night.
_DRIVER = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');

const args = process.argv.slice(2);
const outFile = args.pop(), inFile = args.pop();
const context = {};
context.self = context;
context.globalThis = context;
vm.createContext(context);
for (const file of args) vm.runInContext(fs.readFileSync(file, 'utf8'), context, { filename: file });
const real = context.NovaAstro;

function runOne(c) {
    const nights = {}, events = {};
    let phase = null, night = null;
    const s = Object.assign(Object.create(null), real);
    s.sunEvents = function (date) {
        const ev = real.sunEvents.apply(null, arguments);
        events[date] = ev;
        return ev;
    };
    s.moonPhaseAt = function () {
        phase = real.moonPhaseAt.apply(null, arguments);
        return phase;
    };
    s.observableDuration = function (ra, dec, lat, lon, date, tz, o) {
        const r = real.observableDuration.apply(null, arguments);
        let gap = null;
        if (o.window && o.window.times.length) {
            const curve = real.altAzCurve(ra, dec, lat, lon, o.window.times, o.window.frame);
            gap = Infinity;
            for (let i = 0; i < curve.alt.length; i++) {
                const floor = o.mask ? real.interpolateHorizon(curve.az[i], o.mask, o.threshold) : o.threshold;
                gap = Math.min(gap, Math.abs(curve.alt[i] - floor));
            }
        }
        night = nights[date] = { minutes: r.minutes, alt: r.maxAltitude, gap: gap, phase: phase, sep: null };
        return r;
    };
    s.moonSeparation = function () {
        const sep = real.moonSeparation.apply(null, arguments);
        night.sep = sep;
        return sep;
    };
    context.NovaAstro = s;
    try {
        return { rows: context.NovaOpportunities.forObject(c.opts), nights: nights, events: events };
    } catch (e) {
        return { error: String((e && e.message) || e) };
    } finally {
        context.NovaAstro = real;
    }
}

const cases = JSON.parse(fs.readFileSync(inFile, 'utf8'));
fs.writeFileSync(outFile, JSON.stringify(cases.map(c => Object.assign({ id: c.id }, runOne(c)))));
"""


# --- setup -------------------------------------------------------------------------------

def _edge_dec(lat, altitude):
    """The declination that culminates at `altitude` on the equator side of the zenith."""
    return lat - 90 + altitude if lat >= 0 else lat + 90 - altitude


def _seed(db_session, template_criteria):
    user = db_session.query(DbUser).filter_by(username="default").one()
    db_session.query(AstroObject).filter_by(user_id=user.id).delete()
    catalogue = {o["Object"]: o for o in yaml.safe_load(CONFIG_DEFAULT.read_text())["objects"]}
    for name in OBJECTS:
        o = catalogue[name]
        db_session.add(AstroObject(user_id=user.id, object_name=name,
                                   ra_hours=o["RA (hours)"], dec_deg=o["DEC (degrees)"]))
    edges = {}
    for name, lat, lon, tz, threshold, mask in SITES:
        loc = Location(user_id=user.id, name=name, lat=lat, lon=lon, timezone=tz, altitude_threshold=threshold)
        db_session.add(loc)
        db_session.flush()
        for az, alt in mask:
            db_session.add(HorizonPoint(location_id=loc.id, az_deg=az, alt_min_deg=alt))
        if mask:
            continue
        lines = {"thr": threshold if threshold is not None else GLOBAL_THRESHOLD,
                 "alt": template_criteria["min_max_altitude"]}
        edges[name] = []
        for kind, line in lines.items():
            obj = f"Edge {kind} {name}"
            db_session.add(AstroObject(user_id=user.id, object_name=obj, ra_hours=EDGE_RA,
                                       dec_deg=_edge_dec(lat, line + EDGE_ABOVE)))
            edges[name].append(obj)
    edges["London masked"] = edges["London"]
    db_session.commit()
    astro_context_cache.clear()
    return user, edges


def _set_criteria(db_session, user, criteria):
    prefs = db_session.query(UiPref).filter_by(user_id=user.id).first()
    blob = json.loads(prefs.json_blob) if prefs and prefs.json_blob else {}
    blob["imaging_criteria"] = criteria
    if prefs:
        prefs.json_blob = json.dumps(blob)
    else:
        db_session.add(UiPref(user_id=user.id, json_blob=json.dumps(blob)))
    db_session.commit()
    astro_context_cache.clear()


class _Clock:
    now = BASE


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return _Clock.now.astimezone(tz) if tz else _Clock.now.replace(tzinfo=None)


def _route_phase(date_str, tz_name):
    """The route's unrounded Moon: ephem at 00:00 local on the day after the night."""
    local_tz = pytz.timezone(tz_name)
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    return ephem.Moon(local_tz.localize(datetime.combine(d + timedelta(days=1), datetime.min.time()))
                      .astimezone(pytz.utc)).phase


def _run_node(tmp_path, cases):
    driver = tmp_path / "opportunities_driver.js"
    driver.write_text(_DRIVER)
    cases_file, results_file = tmp_path / "cases.json", tmp_path / "results.json"
    cases_file.write_text(json.dumps(cases, allow_nan=False))
    run = subprocess.run(
        [NODE, str(driver), *[str(STATIC_JS / f) for f in ENGINE_FILES], str(cases_file), str(results_file)],
        capture_output=True, text=True, check=False,
    )
    assert run.returncode == 0, f"node failed ({run.returncode}):\n{run.stderr}"
    return {r["id"]: r for r in json.loads(results_file.read_text())}


# --- the comparison ----------------------------------------------------------------------

def _near_halfway(x, eps):
    """x lies within eps of a halfway point between two one-decimal values."""
    scaled = x * 10
    return abs((scaled - math.floor(scaled)) - 0.5) / 10 < eps


def _edge_nights(server_events, browser_events):
    """{night: reason} where a twilight that builds the night window prints a different
    minute and the browser's instant is within EPS_TWILIGHT_S of that minute, or the
    two printed minutes are exactly one apart."""
    edges = {}
    for night, s in server_events.items():
        b = browser_events.get(night)
        if b is None:
            continue
        uses = {
            "astronomical_dusk": True, "astronomical_dawn": True,
            "sunset": "N/A" in (s["astronomical_dusk"], b["astronomical_dusk"]),
            "sunrise": "N/A" in (s["astronomical_dawn"], b["astronomical_dawn"]),
        }
        for key, used in uses.items():
            if not used or s[key] == b[key]:
                continue
            instant = b.get(f"{key}_utc")
            seconds = instant / 1000 % 60 if instant is not None else None
            near_minute = seconds is not None and min(seconds, 60 - seconds) < EPS_TWILIGHT_S
            one_minute = "N/A" not in (s[key], b[key]) and _clock_gap(s[key], b[key]) == 1
            if near_minute or one_minute:
                edges[night] = f"{key} {s[key]} / {b[key]}"
    return edges


@functools.lru_cache(maxsize=None)
def _sun_minimum(date_str, tz_name, lat, lon):
    """astropy's lowest Sun altitude over the night of date_str: local noon to noon, by the minute."""
    local_tz = pytz.timezone(tz_name)
    noon = local_tz.localize(datetime.strptime(date_str, "%Y-%m-%d").replace(hour=12)).astimezone(pytz.utc)
    t = Time(noon) + np.arange(0, 24 * 60 + 1) * u.min
    loc = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
    return float(get_body("sun", t, location=loc).transform_to(AltAz(obstime=t, location=loc)).alt.deg.min())


def _grazing_nights(case, browser_events):
    """{night: reason} where one side has an astronomical dusk or dawn and the other
    'N/A', and the Sun's lowest point that night is within EPS_GRAZING_DEG of -18."""
    grazing = {}
    for night, s in case["events"].items():
        b = browser_events.get(night)
        if b is None:
            continue
        split = [k for k in ("astronomical_dusk", "astronomical_dawn") if (s[k] == "N/A") != (b[k] == "N/A")]
        if not split:
            continue
        lowest = _sun_minimum(night, case["tz"], case["lat"], case["lon"])
        if abs(lowest + 18) < EPS_GRAZING_DEG:
            grazing[night] = ", ".join(f"{k} {s[k]} / {b[k]}" for k in split) + f", Sun lowest {lowest:.4f}"
    return grazing


def _score(alt, minutes, phase, sep):
    """core.get_imaging_opportunities()'s composite score."""
    score_alt = max(0, min((alt - 20) / 70, 1))
    score_duration = min(minutes * 60 / 43200, 1)
    score_moon_illum = 1 - min(phase / 100, 1)
    score_moon_sep_dynamic = (1 - (phase / 100)) + (phase / 100) * min(sep / 180, 1)
    return 100 * (0.20 * score_alt + 0.15 * score_duration + 0.45 * score_moon_illum + 0.20 * score_moon_sep_dynamic)


def _hm(s):
    return int(s[:2]) * 60 + int(s[3:])


def _clock_gap(a, b):
    d = abs(_hm(a) - _hm(b)) % 1440
    return min(d, 1440 - d)


def _straddles(a, b, line, eps=0.0):
    """a and b fall on either side of `line` (a value within eps of it counts as either)."""
    return (min(a, b) - eps < line <= max(a, b) + eps) or (min(a, b) - eps <= line < max(a, b) + eps)


def _one_sided(date, side, srv, brw, phase, crit, interval, edge, alt_step):
    """The class of a row present on `side` only, or None."""
    s, b = srv[date], brw[date]
    own = s if side == "server" else b
    if abs(phase - crit["max_moon_illumination"]) <= MOON_BAND:
        return "moon"
    if own["sep"] is not None and abs(own["sep"] - crit["min_angular_separation"]) <= EPS_SEP:
        return "separation"
    if any(abs(x - crit["min_max_altitude"]) <= EPS_DEG for x in (s["alt"], b["alt"])):
        return "threshold edge"
    if (abs(s["minutes"] - b["minutes"]) == interval and b["gap"] is not None and b["gap"] < EPS_DEG
            and _straddles(s["minutes"], b["minutes"], crit["min_observable_minutes"])):
        return "threshold edge"
    if edge:
        if abs(s["minutes"] - b["minutes"]) <= interval \
                and _straddles(s["minutes"], b["minutes"], crit["min_observable_minutes"]):
            return "night edge"
        if abs(s["alt"] - b["alt"]) <= alt_step and _straddles(s["alt"], b["alt"], crit["min_max_altitude"]):
            return "night edge"
        if own["sep"] is not None and abs(own["sep"] - crit["min_angular_separation"]) <= EPS_SEP + SEP_PER_MINUTE:
            return "night edge"
    return None


def _compare(case, result, tally, problems):
    cid, crit, interval = case["id"], case["criteria"], case["interval"]
    if "error" in result:
        problems.append(f"{cid}: browser threw: {result['error']}")
        return
    t = tally.setdefault(case["case"], {"rows": 0, "compared": 0, "one-sided": 0, "cases": 0, "seconds": 0.0,
                                        "examples": {}, "worst": {}, "nights": {c: set() for c in CLASSES},
                                        **{c: 0 for c in CLASSES}})
    t["cases"] += 1
    t["seconds"] += case["seconds"]

    def allow(cls, text, date):
        t[cls] += 1
        t["nights"][cls].add(date)
        t["examples"].setdefault(cls, text)

    def worst(name, value, where):
        t["worst"][name] = max(t["worst"].get(name, (0.0, "")), (value, where))

    if case["status"] != "success":
        problems.append(f"{cid}: route status {case['status']!r}")
        return
    server, browser = case["server"], result["rows"]
    srv, brw = case["nights"], result["nights"]
    grazing = _grazing_nights(case, result["events"])
    edges = {k: v for k, v in _edge_nights(case["events"], result["events"]).items() if k not in grazing}
    alt_step = 15 * math.cos(math.radians(case["lat"])) * interval / 60 + 0.1
    s_by, b_by = {r["date"]: r for r in server}, {r["date"]: r for r in browser}
    t["rows"] += len(server)

    for date in sorted(set(srv) & set(brw)):
        if srv[date]["sep"] is not None and brw[date]["sep"] is not None \
                and date not in edges and date not in grazing:
            worst("raw separation", abs(srv[date]["sep"] - brw[date]["sep"]), f"{cid} {date}")

    for side, rows, other in (("server", server, b_by), ("browser", browser, s_by)):
        for r in rows:
            if r["date"] in other:
                continue
            date = r["date"]
            cls = "grazing night" if date in grazing else \
                _one_sided(date, side, srv, brw, case["phases"][date], crit, interval, date in edges, alt_step)
            text = (f"{cid} {date}: {side} only (minutes {srv[date]['minutes']}/{brw[date]['minutes']}, "
                    f"alt {srv[date]['alt']:.4f}/{brw[date]['alt']:.4f}, phase {case['phases'][date]:.4f}, "
                    f"sep {srv[date]['sep']}/{brw[date]['sep']})")
            t["one-sided"] += 1
            if cls:
                allow(cls, text, date)
            else:
                problems.append(text)

    s_common = [r["date"] for r in server if r["date"] in b_by]
    b_common = [r["date"] for r in browser if r["date"] in s_by]
    if s_common != b_common or s_common != sorted(s_common):
        problems.append(f"{cid}: row order differs")
        return

    t["compared"] += len(s_common)
    for date in s_common:
        s, b = s_by[date], b_by[date]
        where = f"{cid} {date}"
        if set(s) != ROW_KEYS or set(b) != ROW_KEYS:
            problems.append(f"{where}: keys {sorted(s)} / {sorted(b)}")
            continue
        if date in grazing:
            for field in sorted(ROW_KEYS - {"date"}):
                if s[field] != b[field]:
                    allow("grazing night", f"{where} {field}: server {s[field]!r}, browser {b[field]!r} "
                                           f"({grazing[date]})", date)
            continue
        rs, rb = srv[date], brw[date]
        edge = date in edges
        note = f" ({edges[date]})" if edge else ""
        d_min = abs(s["obs_minutes"] - b["obs_minutes"])
        threshold_edge = d_min == interval and rb["gap"] is not None and rb["gap"] < EPS_DEG

        def check(field, ok_cls):
            if s[field] == b[field]:
                return
            text = f"{where} {field}: server {s[field]!r}, browser {b[field]!r}{note}"
            if ok_cls:
                allow(ok_cls, text, date)
            else:
                problems.append(text + f" (minutes {rs['minutes']}/{rb['minutes']}, "
                                       f"alt {rs['alt']:.5f}/{rb['alt']:.5f}, gap {rb['gap']})")

        check("obs_minutes", "night edge" if edge and d_min == interval
              else "threshold edge" if threshold_edge else None)

        for field in ("from_time", "to_time"):
            sv, bv = s[field], b[field]
            if sv == bv:
                continue
            if "N/A" in (sv, bv):
                ok = (edge or threshold_edge) and 0 in (s["obs_minutes"], b["obs_minutes"])
            else:
                gap = _clock_gap(sv, bv)
                ok = (edge and gap <= interval + 1) or (threshold_edge and gap == interval)
            check(field, ("night edge" if edge else "threshold edge") if ok else None)

        d_alt = abs(s["max_alt"] - b["max_alt"])
        worst("max_alt", d_alt, where)
        check("max_alt", "night edge" if edge and d_alt <= alt_step + 1e-9
              else "threshold edge" if abs(d_alt - 0.1) < 1e-9 and _near_halfway(rs["alt"], EPS_DEG) else None)

        d_moon = abs(s["moon_illumination"] - b["moon_illumination"])
        check("moon_illumination", "moon" if abs(d_moon - 0.1) < 1e-9
              and _near_halfway(case["phases"][date], EPS_PHASE_EDGE) else None)

        d_sep = abs(s["moon_separation"] - b["moon_separation"])
        worst("moon_separation", d_sep, where)
        sep_eps = EPS_SEP + (SEP_PER_MINUTE if edge else 0)
        check("moon_separation", ("night edge" if edge else "separation") if abs(d_sep - 0.1) < 1e-9
              and _near_halfway(rs["sep"], sep_eps) else None)

        if s["rating"] != b["rating"]:
            stars_s, stars_b = s["rating"].count("★"), b["rating"].count("★")
            score_s = _score(rs["alt"], rs["minutes"], case["phases"][date], rs["sep"])
            score_b = _score(rb["alt"], rb["minutes"], rb["phase"], rb["sep"])
            across = any(_straddles(score_s, score_b, x) for x in STAR_BOUNDARIES)
            tol = SCORE_TOL + (100 * (0.20 * alt_step / 70 + 0.15 * interval * 60 / 43200) if edge or threshold_edge else 0)
            ok = abs(stars_s - stars_b) == 1 and across and abs(score_s - score_b) <= tol
            check("rating", ("night edge" if edge else "rating") if ok else None)


# --- the test ----------------------------------------------------------------------------

@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_nova_opportunities_agrees_with_the_route(su_client_logged_in, db_session, tmp_path, monkeypatch):
    client = su_client_logged_in
    template = dict(yaml.safe_load(CONFIG_DEFAULT.read_text())["imaging_criteria"])
    template["search_horizon_months"] = HORIZON_MONTHS
    user, edge_objects = _seed(db_session, template)
    sites = {s[0]: s for s in SITES}

    nights, current = {}, {}
    real_duration, real_separation = core.calculate_observable_duration_vectorized, BaseCoordinateFrame.separation

    def recording_duration(ra, dec, lat, lon, date_str, tz, thr, step, horizon_mask=None):
        result = real_duration(ra, dec, lat, lon, date_str, tz, thr, step, horizon_mask=horizon_mask)
        current["date"] = date_str
        nights[date_str] = {"minutes": result[0].total_seconds() / 60, "alt": result[1], "sep": None}
        return result

    def recording_separation(self, other, *a, **k):
        result = real_separation(self, other, *a, **k)
        nights[current["date"]]["sep"] = float(result.deg)
        return result

    monkeypatch.setattr(core, "datetime", _FrozenDatetime)
    monkeypatch.setattr(core, "calculate_observable_duration_vectorized", recording_duration)
    monkeypatch.setattr(BaseCoordinateFrame, "separation", recording_separation)

    cases = []
    for label, criteria in (("template", template), ("loose", LOOSE_CRITERIA)):
        _set_criteria(db_session, user, criteria)
        for site, instant in CASES:
            _Clock.now = instant
            _, lat, lon, tz, _, _ = sites[site]
            inputs = client.get("/api/desktop_inputs", query_string={"location": site}).get_json()
            for name in OBJECTS + tuple(edge_objects[site]):
                nights.clear()
                started = time.perf_counter()
                resp = client.get(f"/get_imaging_opportunities/{name}", query_string={"plot_loc_name": site})
                seconds = time.perf_counter() - started
                assert resp.status_code == 200, (site, name, resp.get_data(as_text=True)[:300])
                body = resp.get_json()
                dates = sorted(nights)
                cases.append({
                    "id": f"{label} | {site} @ {instant:%Y-%m-%d %H:%M}Z | {name}",
                    "case": f"{label} | {site} @ {instant:%Y-%m-%d %H:%M}Z",
                    "status": body["status"], "server": body["results"], "nights": dict(nights),
                    "criteria": criteria, "interval": inputs["location"]["sampling_interval"],
                    "lat": lat, "lon": lon, "tz": tz,
                    "phases": {d: _route_phase(d, tz) for d in dates},
                    "events": {d: dict(calculate_sun_events_cached(d, tz, lat, lon)) for d in dates},
                    "seconds": seconds,
                    "opts": {"inputs": inputs, "objectName": name, "nowMs": instant.timestamp() * 1000},
                })

    started = time.perf_counter()
    results = _run_node(tmp_path, [{"id": c["id"], "opts": c["opts"]} for c in cases])
    node_seconds = time.perf_counter() - started

    tally, problems = {}, []
    for case in cases:
        _compare(case, results[case["id"]], tally, problems)

    print(f"\nopportunities parity: {len(cases)} route calls, "
          f"{sum(c['seconds'] for c in cases):.1f} s route, {node_seconds:.1f} s node")
    for name, t in tally.items():
        counts = ", ".join(f"{c} {t[c]} ({len(t['nights'][c])} nights)" for c in CLASSES)
        print(f"  {name}: {t['cases']} objects, {t['rows']} route rows, {t['compared']} compared, "
              f"{t['one-sided']} one-sided, {t['seconds']:.1f} s; {counts}")
        for metric, (value, where) in sorted(t["worst"].items()):
            print(f"      worst {metric}: {value:.4g} at {where}")
        for cls, example in sorted(t["examples"].items()):
            print(f"      e.g. {cls}: {example}")
        for cls, cap in CAPS.items():
            n = len(t["nights"][cls]) if cls in NIGHT_CLASSES else t[cls]
            if n > cap:
                problems.append(f"{name}: {n} {cls}, cap {cap}")

    # The matrix must reach what it claims to cover
    by_case = lambda prefix: [c for c in cases if c["case"].startswith(prefix)]
    every = [r for c in cases for r in c["server"]]
    assert all(t["rows"] > 0 for k, t in tally.items() if k.startswith("loose")), \
        {k: t["rows"] for k, t in tally.items()}
    assert any(r["from_time"] == "N/A" for r in every), "no 'N/A' from/to reached"
    assert {r["rating"].count("★") for r in every} >= {1, 2, 3, 4, 5}, "not every star count reached"
    sg_night = by_case("loose | Singapore @ 2026-10-18 16:30Z")
    assert min(r["date"] for c in sg_night for r in c["server"]) == "2026-10-19"
    london, masked = by_case("loose | London @ 2026-10-18"), by_case("loose | London masked")
    assert [c["server"] for c in london] != [c["server"] for c in masked], "the mask changed nothing"
    spring = [r for c in by_case("loose | London @ 2026-03-20") for r in c["server"] if r["date"] == "2026-03-28"]
    assert any(r["to_time"] != "N/A" for r in spring), "no row on the 28 Mar clock change"
    tromso = by_case("loose | Tromso @ 2026-09-05")
    assert any(e["astronomical_dusk"] == "N/A" for e in tromso[0]["events"].values())
    assert any(e["astronomical_dusk"] != "N/A" for e in tromso[0]["events"].values())
    edge_rows = [r for c in cases if c["id"].split(" | ")[-1].startswith("Edge") for r in c["server"]]
    assert edge_rows, "no synthetic edge object produced a row"
    assert not problems, f"{len(problems)} difference(s):\n" + "\n".join(problems[:40])
