"""The browser's Outlook rows must be the worker's Outlook rows.

static/js/nova_outlook.js (NovaOutlook.opportunities) rebuilds what
update_outlook_cache() writes to the Outlook file, so the table can be filled
without the worker. This pins the two together: a fixed set of the shipped
catalogue marked active project, at London, Singapore, Tromso and Perth, from four
start dates (a winter night, the night Europe changes to summer time, midsummer
with no astronomical night at Tromso, autumn), under the template's imaging
criteria and under a loose set that lets every night with a score above 75 through.

Server: the worker's own steps for one location — _outlook_location_inputs(),
load_outlook_active_objects(), _outlook_start_date() and
compute_outlook_object_opportunities() per object, sorted by date as
update_outlook_cache() sorts — with real ephem and astropy, and the objects coming
from to_dict(), so get_ra_dec() never reaches SIMBAD. The horizon is the first
HORIZON_NIGHTS nights of each start date, not search_horizon_months * 30, to keep
the file near 30 s; every window still holds a new Moon. The worker's 10 ms pause
per object is skipped.
Browser: /api/desktop_inputs for the same user and location, fed to
NovaOutlook.opportunities() with the same sim date; its rows after the horizon are
not compared.

Comparison: the same (object, date) rows in the same order; every string and
boolean identical; numbers within the tolerances below. The tolerances come from
a run of this matrix with 29 objects (every 12th of the catalogue by RA):
  - score: both sides keep the unrounded float, but the browser scores on
    moonPhaseAt()'s one decimal, which moves the score by at most 0.65 * 0.06.
    EPS_SCORE 0.04; measured worst outside an edge: 0.0332 (2572 rows).
  - moon_illumination: one printed step (0.1), only where the worker's unrounded
    phase lies within EPS_PHASE_EDGE of a halfway point. Measured: 0.0084.
  - A night edge: a twilight string that differs by a minute, with the browser's
    instant within EPS_TWILIGHT_S of that minute (measured: 4.45 s at Tromso,
    3.24 s at London) and the night window built from it. The whole sample grid
    moves or gains its last sample, so obs_dur may differ by one sample, max_alt by
    as much as an altitude can change in one sample (15 deg/h * cos(lat) over the
    interval, plus 0.1 for rounding; measured 2.3 at London, bound 2.43), and the
    score by what those two differences move it.
  - A threshold edge: obs_dur may differ by one sample where one of the browser's
    samples lies within EPS_DEG of the altitude threshold; the score follows.
  - rating / rating_num: one star apart, only where the worker's score lies within
    the row's score tolerance of a star boundary (round(score / 25) + 1 turns at
    87.5 above 75).
A row may appear on one side only (the known difference: the browser filters and
scores on moonPhaseAt()'s one decimal, the worker on ephem's unrounded phase)
where its score is within EPS_SCORE of 75, or the worker's unrounded Moon
illumination is within MOON_BAND of max_moon_illumination, or on a night edge
where its score is within EPS_SCORE plus the most a night edge can move it (one
sample of max_alt and of obs_dur) of 75. Every allowed case is counted and
printed per site; anything else fails.
"""

import json
import math
import shutil
import subprocess
import time
import types
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path

import ephem
import pytest
import pytz
import yaml

import nova
from nova import app
from nova.config import astro_context_cache
from nova.models import AstroObject, DbUser, Location, SavedFraming, UiPref
from modules.astro_calculations import calculate_sun_events_cached

REPO = Path(__file__).resolve().parent.parent
STATIC_JS = REPO / "static" / "js"
CONFIG_DEFAULT = REPO / "config_templates" / "config_default.yaml"
NODE = shutil.which("node")
ENGINE_FILES = ("nova_astro.js", "nova_dashboard_rows.js", "nova_outlook.js")

SITES = [
    ("London", 51.509865, -0.118092, "Europe/London"),
    ("Singapore", 1.3521, 103.8198, "Asia/Singapore"),
    ("Tromso", 69.6492, 18.9553, "Europe/Oslo"),
    ("Perth", -31.9522, 115.8614, "Australia/Perth"),
]
# 2026-03-29 01:00 UTC Europe changes to summer time; 2026-06-21 Tromso has no
# astronomical night.
START_DATES = ("2026-01-15", "2026-03-28", "2026-06-21", "2026-10-17")
HORIZON_NIGHTS = 30

# Catalogue objects that give rows at two to four of the sites, both circumpolar
# ones, and the two that sit on a night edge (IC1318) and a threshold edge (C33).
OBJECTS = ("NGC7822", "NGC2264", "M65", "M101", "M104", "C57", "C109", "IC1318", "C33")
FRAMED = "M101"
PROJECT_NOTES = {"NGC2264": "Cone, two panels"}

LOOSE_CRITERIA = {"min_observable_minutes": 0, "min_max_altitude": 0, "max_moon_illumination": 100,
                  "min_angular_separation": 0, "search_horizon_months": 1}

EPS_SCORE = 0.04
EPS_PHASE_EDGE = 0.01
EPS_TWILIGHT_S = 5.0
EPS_DEG = 0.002
MOON_BAND = 0.05
SCORING_WINDOW_SECONDS = 43200
STAR_BOUNDARIES = (12.5, 37.5, 62.5, 87.5)

STRING_FIELDS = ("object_name", "common_name", "has_framing", "date", "project", "type",
                 "constellation", "magnitude", "size", "sb")
NUMERIC_FIELDS = ("score", "max_alt", "obs_dur", "moon_illumination")

# Loads the three files the way the page does — plain scripts in one context. Per
# case: the rows and the browser's sun events for every compared night; or, for the
# (object, night) pairs asked about, how close a sample of the night comes to the
# altitude threshold.
_DRIVER = """
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
const A = context.NovaAstro, D = context.NovaDashboardRows;
const cases = JSON.parse(fs.readFileSync(inFile, 'utf8'));
fs.writeFileSync(outFile, JSON.stringify(cases.map(c => {
    try {
        const loc = c.opts.inputs.location;
        if (c.kind === 'samples') {
            return { id: c.id, data: c.pairs.map(p => {
                const ev = A.sunEvents(p.date, loc.timezone, loc.lat, loc.lon);
                const w = D._wall.nightWindow(p.date, loc.timezone, loc.sampling_interval, ev, A);
                if (!w) return null;
                const curve = A.altAzCurve(p.ra, p.dec, loc.lat, loc.lon, w.times, w.frame);
                let gap = Infinity;
                for (const alt of curve.alt) gap = Math.min(gap, Math.abs(alt - loc.altitude_threshold));
                return gap;
            }) };
        }
        const events = {};
        for (const night of c.nights) events[night] = A.sunEvents(night, loc.timezone, loc.lat, loc.lon);
        return { id: c.id, data: { rows: context.NovaOutlook.opportunities(c.opts), events: events } };
    } catch (e) {
        return { id: c.id, error: String(e && e.message || e) };
    }
})));
"""


# --- setup -------------------------------------------------------------------------------

def _seed(db_session):
    template = yaml.safe_load(CONFIG_DEFAULT.read_text())
    catalogue = {o["Object"]: o for o in template["objects"]}
    user = db_session.query(DbUser).filter_by(username="default").one()
    for name in OBJECTS:
        o = catalogue[name]
        db_session.add(AstroObject(
            user_id=user.id, object_name=name, common_name=o.get("Common Name"),
            ra_hours=o["RA (hours)"], dec_deg=o["DEC (degrees)"], type=o.get("Type"),
            constellation=o.get("Constellation"), magnitude=o.get("Magnitude"), size=o.get("Size"),
            sb=o.get("SB"), active_project=True, project_name=PROJECT_NOTES.get(name)))
    for name, lat, lon, tz in SITES:
        db_session.add(Location(user_id=user.id, name=name, lat=lat, lon=lon, timezone=tz))
    db_session.commit()
    framed = db_session.query(AstroObject).filter_by(user_id=user.id, object_name=FRAMED).one()
    db_session.add(SavedFraming(user_id=user.id, object_name=FRAMED, object_record_uid=framed.record_uid))
    db_session.commit()
    return user, template["imaging_criteria"]


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


def _server_rows(user, location_name, sim_date):
    """update_outlook_cache()'s calculation for the first HORIZON_NIGHTS nights."""
    with app.app_context():
        cfg = nova.build_user_config_from_db("default")
        inputs = nova._outlook_location_inputs(location_name, cfg, "parity")
        project_objects, objects_map, framed = nova.load_outlook_active_objects(user.id, "parity")
        local_tz = pytz.timezone(inputs["tz_name"])
        start = nova._outlook_start_date(inputs["tz_name"], sim_date)
        dates = [start + timedelta(days=i) for i in range(HORIZON_NIGHTS)]
        rows = []
        for obj in project_objects:
            rows.extend(nova.compute_outlook_object_opportunities(
                obj, objects_map, framed, inputs, local_tz, dates, nova.resolve_sampling_interval(cfg),
                "parity"))
        # The worker's unrounded Moon (compute_outlook_object_opportunities, same expression)
        phases = {d.isoformat(): ephem.Moon(local_tz.localize(datetime.combine(
            d + timedelta(days=1), datetime.min.time())).astimezone(pytz.utc)).phase for d in dates}
        events = {d.isoformat(): dict(calculate_sun_events_cached(
            d.isoformat(), inputs["tz_name"], inputs["lat"], inputs["lon"])) for d in dates}
        return sorted(rows, key=lambda r: r["date"]), inputs["criteria"], phases, events


def _run_node(tmp_path, cases):
    driver = tmp_path / "outlook_driver.js"
    driver.write_text(_DRIVER)
    cases_file = tmp_path / "cases.json"
    results_file = tmp_path / "results.json"
    cases_file.write_text(json.dumps(cases))
    run = subprocess.run(
        [NODE, str(driver), *[str(STATIC_JS / f) for f in ENGINE_FILES],
         str(cases_file), str(results_file)],
        capture_output=True, text=True, check=False,
    )
    assert run.returncode == 0, f"node failed ({run.returncode}):\n{run.stderr}"
    return {r["id"]: r for r in json.loads(results_file.read_text())}


# --- the comparison rule -----------------------------------------------------------------

def _near_halfway(x, eps):
    """x lies within eps of a halfway point between two one-decimal values."""
    scaled = x * 10
    return abs((scaled - math.floor(scaled)) - 0.5) / 10 < eps


def _edge_nights(case, browser_events):
    """{night: reason} where a twilight that builds the night window prints a different
    minute and the browser's instant is within EPS_TWILIGHT_S of that minute."""
    edges = {}
    for night, server in case["events"].items():
        browser = browser_events[night]
        uses = {
            "astronomical_dusk": True, "astronomical_dawn": True,
            "sunset": "N/A" in (server["astronomical_dusk"], browser["astronomical_dusk"]),
            "sunrise": "N/A" in (server["astronomical_dawn"], browser["astronomical_dawn"]),
        }
        for key, used in uses.items():
            if not used or server[key] == browser[key]:
                continue
            instant = browser.get(f"{key}_utc")
            seconds = instant / 1000 % 60 if instant is not None else None
            if seconds is not None and min(seconds, 60 - seconds) < EPS_TWILIGHT_S:
                edges[night] = f"{key} {server[key]} / {browser[key]}"
    return edges


def _score_shift(d_alt, d_dur):
    """How far the score moves when max_alt moves by d_alt and obs_dur by d_dur minutes."""
    return 100 * (0.20 * d_alt / 70 + 0.15 * d_dur * 60 / SCORING_WINDOW_SECONDS)


def _moon_reason(s, b, phase):
    """One printed step, only where the worker's unrounded phase sits on a halfway point."""
    if abs(s["moon_illumination"] - b["moon_illumination"]) <= 0.1 + 1e-9 \
            and _near_halfway(phase, EPS_PHASE_EDGE):
        return "rounding edge"
    return None


def _one_sided_reason(row, phase, crit, night_edge, edge_shift):
    if row["score"] - 75 <= EPS_SCORE:
        return "score within EPS_SCORE of 75"
    if abs(phase - crit["max_moon_illumination"]) <= MOON_BAND:
        return "Moon within MOON_BAND of the limit"
    if night_edge and row["score"] - 75 <= EPS_SCORE + edge_shift:
        return "night edge, score within its shift of 75"
    return None


def _compare(case, result, threshold_gaps, tally, problems):
    cid, site, crit = case["id"], case["site"], case["criteria"]
    if "error" in result:
        problems.append(f"{cid}: browser threw: {result['error']}")
        return
    nights = set(case["phases"])
    server = case["server"]
    browser = [r for r in result["data"]["rows"] if r["date"] in nights]
    edges = _edge_nights(case, result["data"]["events"])
    key = lambda r: (r["object_name"], r["date"])
    s_rows, b_rows = {key(r): r for r in server}, {key(r): r for r in browser}
    tally["rows"][site] += len(server)

    def allow(field, reason, text):
        tally["allowed"].setdefault((site, field, reason), []).append(text)

    lat = case["opts"]["inputs"]["location"]["lat"]
    interval = case["opts"]["inputs"]["location"]["sampling_interval"]
    max_alt_step = 15 * math.cos(math.radians(lat)) * interval / 60 + 0.1
    edge_shift = _score_shift(max_alt_step, interval)

    for side, rows, other in (("server", server, b_rows), ("browser", browser, s_rows)):
        for r in rows:
            if key(r) in other:
                continue
            phase = case["phases"][r["date"]]
            reason = _one_sided_reason(r, phase, crit, r["date"] in edges, edge_shift)
            if reason:
                allow(f"{side}-only row", reason, f"{cid} {key(r)} score {r['score']:.4f}, phase {phase:.4f}")
            else:
                problems.append(f"{cid} {key(r)}: only on the {side} side (score {r['score']}, "
                                f"phase {phase:.4f})")

    s_common = [key(r) for r in server if key(r) in b_rows]
    b_common = [key(r) for r in browser if key(r) in s_rows]
    if s_common != b_common:
        problems.append(f"{cid}: row order differs")
        return

    for k in s_common:
        s, b = s_rows[k], b_rows[k]
        where = f"{cid} {k}"
        if set(s) != set(b):
            problems.append(f"{where}: keys {sorted(set(s) ^ set(b))} differ")
            continue
        for field in STRING_FIELDS:
            if type(s[field]) is not type(b[field]) or s[field] != b[field]:
                problems.append(f"{where} {field}: server {s[field]!r}, browser {b[field]!r}")

        night_edge = k[1] in edges
        d_alt, d_dur = abs(s["max_alt"] - b["max_alt"]), abs(s["obs_dur"] - b["obs_dur"])
        threshold_edge = bool(d_dur) and threshold_gaps.get((cid, k), math.inf) < EPS_DEG
        edge_note = f" ({edges[k[1]]})" if night_edge else ""
        # The score tolerance of this row: the Moon's decimal, plus what its own
        # max_alt and obs_dur differences move the score on an edge.
        score_tol = EPS_SCORE + (_score_shift(d_alt + 0.1, d_dur) if night_edge or threshold_edge else 0)
        d_score = abs(s["score"] - b["score"])

        reasons = {
            "moon_illumination": _moon_reason(s, b, case["phases"][k[1]]),
            "max_alt": ("night edge" if night_edge and d_alt <= max_alt_step + 1e-9
                        else "rounding edge" if d_alt <= 0.1 + 1e-9 else None),
            "obs_dur": ("night edge" if night_edge and d_dur <= interval
                        else "threshold edge" if threshold_edge and d_dur <= interval else None),
            "score": ("within EPS_SCORE" if d_score <= EPS_SCORE
                      else "follows max_alt/obs_dur" if d_score <= score_tol else None),
        }
        for field in NUMERIC_FIELDS:
            gap = abs(s[field] - b[field])
            tally["worst"][field] = max(tally["worst"].get(field, (0, "")), (gap, where))
            if s[field] == b[field]:
                continue
            if reasons[field]:
                allow(field, reasons[field], f"{where}: server {s[field]!r}, browser {b[field]!r}{edge_note}")
            else:
                problems.append(f"{where} {field}: server {s[field]!r}, browser {b[field]!r}{edge_note}")

        if s["rating_num"] != b["rating_num"] or s["rating"] != b["rating"]:
            consistent = b["rating"] == "★" * b["rating_num"] + "☆" * (5 - b["rating_num"])
            near_boundary = min(abs(s["score"] - x) for x in STAR_BOUNDARIES) <= score_tol
            if consistent and abs(s["rating_num"] - b["rating_num"]) == 1 and near_boundary:
                allow("rating", "star boundary", f"{where}: server {s['rating']} ({s['score']:.4f}), "
                                                 f"browser {b['rating']} ({b['score']:.4f}){edge_note}")
            else:
                problems.append(f"{where} rating: server {s['rating']!r} ({s['score']}), "
                                f"browser {b['rating']!r} ({b['score']})")


# --- tests -------------------------------------------------------------------------------

@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_nova_outlook_agrees_with_the_worker(client, db_session, tmp_path, monkeypatch):
    user, template_criteria = _seed(db_session)
    # The worker pauses 10 ms per object; nova/__init__ alone sees a time without it.
    monkeypatch.setattr(nova, "time", types.SimpleNamespace(**{**vars(time), "sleep": lambda _s: None}))

    cases = []
    for label, criteria in (("template", template_criteria), ("loose", LOOSE_CRITERIA)):
        _set_criteria(db_session, user, criteria)
        for site, *_ in SITES:
            inputs = client.get('/api/desktop_inputs', query_string={"location": site}).get_json()
            for sim_date in START_DATES:
                rows, crit, phases, events = _server_rows(user, site, sim_date)
                cases.append({
                    "id": f"{label} | {site} | {sim_date}", "site": site, "criteria": crit,
                    "server": rows, "phases": phases, "events": events,
                    "opts": {"inputs": inputs, "simDate": sim_date,
                             "nowMs": datetime(2026, 10, 17, 12, tzinfo=dt_timezone.utc).timestamp() * 1000},
                })
    results = _run_node(tmp_path, [{"id": c["id"], "kind": "rows", "opts": c["opts"],
                                    "nights": sorted(c["phases"])} for c in cases])

    # The browser's samples for every compared row whose obs_dur differs.
    asks = []
    for c in cases:
        b_rows = {(r["object_name"], r["date"]): r for r in results[c["id"]].get("data", {}).get("rows", [])}
        objects = {o["Object"]: o for o in c["opts"]["inputs"]["objects"]}
        pairs = [(r["object_name"], r["date"]) for r in c["server"]
                 if (r["object_name"], r["date"]) in b_rows
                 and b_rows[(r["object_name"], r["date"])]["obs_dur"] != r["obs_dur"]]
        if pairs:
            asks.append((c, pairs, {"id": c["id"], "kind": "samples", "opts": c["opts"], "pairs": [
                {"date": d, "ra": objects[o]["ra"], "dec": objects[o]["dec"]} for o, d in pairs]}))
    gaps = _run_node(tmp_path, [a[2] for a in asks]) if asks else {}
    threshold_gaps = {(c["id"], pair): gap for c, pairs, ask in asks
                      for pair, gap in zip(pairs, gaps[c["id"]]["data"]) if gap is not None}

    tally = {"rows": {site: 0 for site, *_ in SITES}, "allowed": {}, "worst": {}}
    problems = []
    for case in cases:
        _compare(case, results[case["id"]], threshold_gaps, tally, problems)

    # The matrix must reach every row shape and a night edge, or it proves less than it says.
    every_row = [r for c in cases for r in c["server"]]
    assert all(n >= 100 for n in tally["rows"].values()), tally["rows"]
    assert any(r["has_framing"] for r in every_row) and any(not r["has_framing"] for r in every_row)
    assert any(r["project"] for r in every_row) and any(r["project"] is None for r in every_row)
    assert {r["rating_num"] for r in every_row} >= {4, 5}
    assert any(reason == "night edge" for _, _, reason in tally["allowed"])

    print(f"\n{len(cases)} cases, rows per site (server): {tally['rows']}")
    for field, (gap, where) in sorted(tally["worst"].items()):
        print(f"  worst {field}: {gap:.4g} at {where}")
    for (site, field, reason), where in sorted(tally["allowed"].items()):
        print(f"  allowed: {site} {field} ({reason}): {len(where)}, e.g. {where[0]}")
    assert not problems, f"{len(problems)} difference(s):\n" + "\n".join(problems[:40])
