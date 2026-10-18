"""The browser's heatmap chunks must be the route's heatmap chunks.

static/js/nova_heatmap.js (NovaHeatmap.chunks) rebuilds what
get_yearly_heatmap_chunk() returns for chunk_index 0..11, so the heatmap can be
drawn without the server. This pins the two together: every STRIDE-th object of
the shipped catalogue plus two objects at the same RA (added in reverse name order,
one with a "nan" magnitude) and one disabled object, at London, Singapore, Tromso
(with its own altitude threshold), Perth and London behind a horizon mask, for a
full year, and Singapore once more just after its Sunday->Monday midnight.

STRIDE keeps the file near 30 s: the route takes about 1 ms per cell, 92 s for the
whole catalogue. The weeks with a night edge or a Moon rounding difference depend on
the site and the date only, so the subset reaches the same weeks.

Measured with the whole catalogue (STRIDE 1), per case: cells / night edge weeks
(cells, largest difference) / Moon weeks (cells, largest) / threshold edge /
printed rounding:
  London      11804 / 1 (89, 7.6) / 3 (166, 0.3) / 0 / 7
  Singapore   17264 / 2 (83, 4.3) / 2 (212, 0.3) / 0 / 8   (both instants)
  Tromso       9048 / 1 (1, 0.1)  / 1 (0, -)     / 0 / 3
  Perth       13624 / 1 (29, 3.1) / 2 (191, 0.3) / 0 / 3
  London mask 11804 / 1 (76, 0.1) / 3 (155, 0.3) / 0 / 5
No threshold edge was reached in either run: the class is there so that one, when
the catalogue or the engine moves a sample onto the threshold, is not a failure.

Server: the route itself through the test client, 12 chunks per case, with
datetime.now() frozen in nova.blueprints.api and the duration calculation
recorded (real astropy and ephem underneath), so every cell's observable minutes
and true maximum altitude are known. Key order is read from the chunk file the
route writes: jsonify sorts the keys on the wire, json.dump keeps the route's.
Browser: /api/desktop_inputs?location=... and /api/desktop_library from the same
client, fed to NovaHeatmap.chunks() with the same instant; observableDuration is
recorded the same way, with how close a sample of the night comes to the
threshold or the mask.

Must match exactly: the keys and their order, chunk_index, x, dates, y, ids,
active, types, cons, mags/sizes/sbs (NaN with NaN) and the row order. A Moon
phase may differ by exactly one printed step (0.1). A score cell may differ only
for one of these reasons, each counted per case and capped, anything else fails:
  - night edge: the week's twilight strings differ (a string the night window
    is built from, the browser's instant within EPS_TWILIGHT_S of the minute);
    the whole sample grid moves, so any cell of that week may differ, by at most
    NIGHT_EDGE_DELTA (one 60-minute sample is 7.5 points before the Moon cut);
  - threshold edge: the two sides count a different number of samples (one
    sample, 60 minutes) and one of the browser's samples lies within EPS_DEG of
    the threshold or the mask; or the two maximum altitudes fall on either side
    of the threshold, one of them within EPS_DEG of it; 0 against a score is
    allowed there;
  - Moon rounding: the week's phases differ by 0.1, the minutes agree and the
    cell differs by at most MOON_DELTA (score * 0.1 / 40 * 0.9 at full Moon,
    plus a printed step);
  - printed rounding: the minutes agree and the cell differs by exactly 0.1.
"""

import json
import math
import shutil
import subprocess
import time
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

import pytest
import yaml

import nova.blueprints.api as api
from nova.config import astro_context_cache
from nova.models import AstroObject, DbUser, HorizonPoint, Location
from modules.astro_calculations import calculate_sun_events_cached

REPO = Path(__file__).resolve().parent.parent
STATIC_JS = REPO / "static" / "js"
CONFIG_DEFAULT = REPO / "config_templates" / "config_default.yaml"
NODE = shutil.which("node")
ENGINE_FILES = ("nova_astro.js", "nova_dashboard_rows.js", "nova_heatmap.js")

# (name, lat, lon, tz, own altitude threshold or None, horizon mask)
MASK = [[0.0, 15.0], [45.0, 30.0], [100.0, 24.0], [160.0, 12.0], [200.0, 28.0], [250.0, 35.0], [320.0, 22.0]]
SITES = [
    ("London", 51.509865, -0.118092, "Europe/London", None, []),
    ("Singapore", 1.3521, 103.8198, "Asia/Singapore", None, []),
    ("Tromso", 69.6492, 18.9553, "Europe/Oslo", 25.0, []),
    ("Perth", -31.9522, 115.8614, "Australia/Perth", None, []),
    ("London masked", 51.509865, -0.118092, "Europe/London", None, MASK),
]
# 2026-10-18 12:00 UTC is a Sunday afternoon or evening at every site, so each grid
# starts on Monday 2026-10-12. 16:00:30 UTC is 00:00:30 on Monday in Singapore:
# that grid starts on 2026-10-19.
SUNDAY = datetime(2026, 10, 18, 12, 0, tzinfo=dt_timezone.utc)
SG_MONDAY = datetime(2026, 10, 18, 16, 0, 30, tzinfo=dt_timezone.utc)
CASES = [(site[0], SUNDAY) for site in SITES] + [("Singapore", SG_MONDAY)]

# Two objects at one RA, added Zeta first, so id order is the reverse of name order
TIES = [
    dict(object_name="Tie Zeta", common_name="Zeta pair", ra_hours=6.0, dec_deg=10.0, type="Neb",
         constellation="Ori", magnitude="1_0", size="12", sb="21"),
    dict(object_name="Tie Alpha", common_name=None, ra_hours=6.0, dec_deg=10.0001, type=None,
         constellation=None, magnitude="nan", size=" 8.5 ", sb="N/A"),
]
DISABLED = "M31"
# Every STRIDE-th catalogue object (by its place in the template), plus DISABLED
STRIDE = 3

EPS_TWILIGHT_S = 5.0
EPS_DEG = 0.002
MOON_DELTA = 0.3
NIGHT_EDGE_DELTA = 8.0
# Per case (one site at one instant). Measured, most in any one case: full catalogue
# night edge 2 weeks, Moon 3 weeks, threshold edge 0, printed rounding 8; STRIDE 3
# the same weeks and printed rounding 2.
CAPS = {
    "night edge weeks": 4,
    "moon weeks": 5,
    "threshold edge": 5,
    "printed rounding": 10,
}
EXACT_FIELDS = ("chunk_index", "x", "dates", "y", "ids", "active", "types", "cons")
FLOAT_FIELDS = ("mags", "sizes", "sbs")

# Loads the three files the way the page does. Per case: the 12 chunks; per cell the
# browser's minutes, max altitude and the closest any sample comes to the threshold
# or the mask (null where the week has no night window); the sun events of each
# week. Non-finite numbers come back as {"num": "..."}.
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
    const loc = c.opts.inputs.location;
    const mask = Array.isArray(loc.horizon_mask) && loc.horizon_mask.length > 1 ? loc.horizon_mask : null;
    const cells = new Map();
    const s = Object.assign(Object.create(null), real);
    s.observableDuration = (ra, dec, lat, lon, date, tz, o) => {
        const r = real.observableDuration(ra, dec, lat, lon, date, tz, o);
        const curve = real.altAzCurve(ra, dec, lat, lon, o.window.times, o.window.frame);
        let gap = Infinity;
        for (let i = 0; i < curve.alt.length; i++) {
            const min = mask ? real.interpolateHorizon(curve.az[i], mask, o.threshold) : o.threshold;
            gap = Math.min(gap, Math.abs(curve.alt[i] - min));
        }
        cells.set(ra + '|' + dec + '|' + date, [r.minutes, r.maxAltitude, gap]);
        return r;
    };
    context.NovaAstro = s;
    try {
        const chunks = context.NovaHeatmap.chunks(c.opts);
        const place = new Map(c.opts.inputs.objects.map(o => [o.Object, o]));
        const info = chunks.map(ch => ch.ids.map(id => ch.dates.map(d => {
            const o = place.get(id);
            return cells.get(o.ra + '|' + o.dec + '|' + d) || null;
        })));
        const events = {};
        for (const ch of chunks) for (const d of ch.dates) events[d] = real.sunEvents(d, loc.timezone, loc.lat, loc.lon);
        return { id: c.id, chunks: chunks, keys: chunks.map(ch => Object.keys(ch)), info: info, events: events };
    } catch (e) {
        return { id: c.id, error: String((e && e.message) || e) };
    } finally {
        context.NovaAstro = real;
    }
}

const encode = (k, v) => (typeof v === 'number' && !isFinite(v)) ? { num: String(v) } : v;
const cases = JSON.parse(fs.readFileSync(inFile, 'utf8'));
fs.writeFileSync(outFile, JSON.stringify(cases.map(runOne), encode));
"""


def _decode(v):
    if isinstance(v, dict) and set(v) == {"num"}:
        return {"NaN": math.nan, "Infinity": math.inf, "-Infinity": -math.inf}[v["num"]]
    if isinstance(v, dict):
        return {k: _decode(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_decode(x) for x in v]
    return v


def _run_node(tmp_path, cases):
    driver = tmp_path / "heatmap_parity_driver.js"
    driver.write_text(_DRIVER)
    cases_file, results_file = tmp_path / "cases.json", tmp_path / "results.json"
    cases_file.write_text(json.dumps(cases, allow_nan=False))
    run = subprocess.run(
        [NODE, str(driver), *[str(STATIC_JS / f) for f in ENGINE_FILES], str(cases_file), str(results_file)],
        capture_output=True, text=True, check=False,
    )
    assert run.returncode == 0, f"node failed ({run.returncode}):\n{run.stderr}"
    return {r["id"]: _decode(r) for r in json.loads(results_file.read_text())}


# --- setup -------------------------------------------------------------------------------

def _seed(db_session):
    user = db_session.query(DbUser).filter_by(username="default").one()
    # su_client_logged_in adds its own M42; the catalogue brings it again, in its own place
    db_session.query(AstroObject).filter_by(user_id=user.id).delete()
    catalogue = yaml.safe_load(CONFIG_DEFAULT.read_text())["objects"]
    for o in [o for i, o in enumerate(catalogue) if i % STRIDE == 0 or o["Object"] == DISABLED]:
        db_session.add(AstroObject(
            user_id=user.id, object_name=o["Object"], common_name=o.get("Common Name"),
            ra_hours=o["RA (hours)"], dec_deg=o["DEC (degrees)"], type=o.get("Type"),
            constellation=o.get("Constellation"), magnitude=o.get("Magnitude"), size=o.get("Size"),
            sb=o.get("SB"), enabled=o["Object"] != DISABLED))
        db_session.flush()
    for tie in TIES:
        db_session.add(AstroObject(user_id=user.id, enabled=True, **tie))
        db_session.flush()
    for name, lat, lon, tz, threshold, mask in SITES:
        loc = Location(user_id=user.id, name=name, lat=lat, lon=lon, timezone=tz, altitude_threshold=threshold)
        db_session.add(loc)
        db_session.flush()
        for az, alt in mask:
            db_session.add(HorizonPoint(location_id=loc.id, az_deg=az, alt_min_deg=alt))
    db_session.commit()
    astro_context_cache.clear()
    return user


class _Clock:
    now = SUNDAY


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return _Clock.now.astimezone(tz) if tz else _Clock.now.replace(tzinfo=None)


def _server_case(client, site, instant, recorded, paths):
    _Clock.now = instant
    recorded.clear()
    paths.clear()
    chunks = []
    for i in range(12):
        resp = client.get("/api/get_yearly_heatmap_chunk", query_string={"chunk_index": i, "location_name": site})
        assert resp.status_code == 200, (site, i, resp.get_data(as_text=True)[:300])
        chunks.append(resp.get_json())
    assert len(paths) == 12 and all(Path(p).exists() for p in paths)
    key_order = [list(json.loads(Path(p).read_text()).keys()) for p in paths]
    return chunks, key_order, dict(recorded)


# --- the comparison ----------------------------------------------------------------------

def _same_float(a, b):
    a, b = float(a), float(b)
    return (math.isnan(a) and math.isnan(b)) or a == b


def _edge_weeks(server_events, browser_events):
    """{date: reason} where a twilight the night window is built from prints a different
    minute and the browser's instant is within EPS_TWILIGHT_S of that minute."""
    edges = {}
    for d, s in server_events.items():
        b = browser_events[d]
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
            if seconds is not None and min(seconds, 60 - seconds) < EPS_TWILIGHT_S:
                edges[d] = f"{key} {s[key]} / {b[key]}"
    return edges


def _compare(case, result, tally, problems):
    cid = case["id"]
    if "error" in result:
        problems.append(f"{cid}: browser threw: {result['error']}")
        return
    server, browser = case["server"], result["chunks"]
    threshold = case["threshold"]
    t = tally.setdefault(cid, {"cells": 0, "night edge weeks": set(), "moon weeks": set(),
                                "night edge cells": 0, "night edge max": 0.0, "threshold edge": 0,
                                "moon cells": 0, "moon max": 0.0, "printed rounding": 0, "examples": {}})
    if len(server) != 12 or len(browser) != 12:
        problems.append(f"{cid}: {len(server)} server chunks, {len(browser)} browser chunks")
        return

    edges = _edge_weeks(case["events"], result["events"])
    for i, (s, b) in enumerate(zip(server, browser)):
        where = f"{cid} chunk {i}"
        if result["keys"][i] != case["key_order"][i] or set(s) != set(b):
            problems.append(f"{where}: keys {result['keys'][i]} vs route {case['key_order'][i]}")
            continue
        for field in EXACT_FIELDS:
            if s[field] != b[field]:
                problems.append(f"{where} {field}: server {s[field]!r:.200}, browser {b[field]!r:.200}")
        for field in FLOAT_FIELDS:
            if len(s[field]) != len(b[field]) or not all(map(_same_float, s[field], b[field])):
                problems.append(f"{where} {field}: server {s[field]!r:.200}, browser {b[field]!r:.200}")
        if s["ids"] != b["ids"] or s["dates"] != b["dates"]:
            continue

        moon_weeks = set()
        for d, ps, pb in zip(s["dates"], s["moon_phases"], b["moon_phases"]):
            if ps == pb:
                continue
            if abs(abs(ps - pb) - 0.1) < 1e-9:
                moon_weeks.add(d)
                t["moon weeks"].add(d)
            else:
                problems.append(f"{where} moon {d}: server {ps}, browser {pb}")

        for k, obj in enumerate(s["ids"]):
            place = case["places"][obj]
            for w, d in enumerate(s["dates"]):
                t["cells"] += 1
                zs, zb = s["z_chunk"][k][w], b["z_chunk"][k][w]
                if zs == zb:
                    continue
                cell = f"{where} {obj} {d}: server {zs}, browser {zb}"
                delta = abs(zs - zb)
                rec_s = case["recorded"].get((place[0], place[1], d))
                rec_b = result["info"][i][k][w]
                if rec_s is None or rec_b is None:
                    problems.append(f"{cell} (no record: server {rec_s}, browser {rec_b})")
                    continue
                s_min, s_alt = rec_s
                b_min, b_alt, gap = rec_b
                same_minutes = s_min == b_min
                if d in edges and delta > NIGHT_EDGE_DELTA + 1e-9:
                    problems.append(f"{cell}: night edge ({edges[d]}) moved it more than NIGHT_EDGE_DELTA")
                elif d in edges:
                    t["night edge weeks"].add(d)
                    t["night edge cells"] += 1
                    t["night edge max"] = max(t["night edge max"], delta)
                    t["examples"].setdefault("night edge", f"{cell} ({edges[d]})")
                elif (abs(s_min - b_min) == 60 and gap < EPS_DEG) or (
                        (s_alt < threshold) != (b_alt < threshold)
                        and min(abs(s_alt - threshold), abs(b_alt - threshold)) < EPS_DEG):
                    t["threshold edge"] += 1
                    t["examples"].setdefault("threshold edge", f"{cell} (minutes {s_min}/{b_min}, "
                                                               f"max alt {s_alt:.4f}/{b_alt:.4f}, gap {gap:.5f})")
                elif d in moon_weeks and same_minutes and delta <= MOON_DELTA + 1e-9:
                    t["moon cells"] += 1
                    t["moon max"] = max(t["moon max"], delta)
                    t["examples"].setdefault("moon rounding", cell)
                elif same_minutes and abs(delta - 0.1) < 1e-9:
                    t["printed rounding"] += 1
                    t["examples"].setdefault("printed rounding", f"{cell} (max alt {s_alt:.5f}/{b_alt:.5f})")
                else:
                    problems.append(f"{cell} (minutes {s_min}/{b_min}, max alt {s_alt:.4f}/{b_alt:.4f}, "
                                    f"gap {gap:.5f}, moon week {d in moon_weeks})")


# --- the test ----------------------------------------------------------------------------

@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_nova_heatmap_agrees_with_the_route(su_client_logged_in, db_session, tmp_path, monkeypatch):
    client = su_client_logged_in
    user = _seed(db_session)
    objects = db_session.query(AstroObject).filter_by(user_id=user.id).all()
    places = {o.object_name: (float(o.ra_hours), float(o.dec_deg)) for o in objects}
    assert len(set(places.values())) == len(places), "two objects share RA and Dec: cells would collide"

    recorded, paths = {}, []
    real_duration, real_path = api.calculate_observable_duration_vectorized, api.heatmap_cache_path

    def recording_duration(ra, dec, lat, lon, date_str, tz, thr, step, horizon_mask=None):
        result = real_duration(ra, dec, lat, lon, date_str, tz, thr, step, horizon_mask)
        recorded[(ra, dec, date_str)] = (result[0].total_seconds() / 60, result[1])
        return result

    def recording_path(*a, **k):
        path = real_path(*a, **k)
        paths.append(path)
        return path

    monkeypatch.setattr(api, "datetime", _FrozenDatetime)
    monkeypatch.setattr(api, "calculate_observable_duration_vectorized", recording_duration)
    monkeypatch.setattr(api, "heatmap_cache_path", recording_path)

    library = client.get("/api/desktop_library").get_json()
    sites = {s[0]: s for s in SITES}
    cases, timings = [], {}
    for site, instant in CASES:
        started = time.perf_counter()
        chunks, key_order, rec = _server_case(client, site, instant, recorded, paths)
        timings[f"{site} {instant:%H:%M:%S}"] = time.perf_counter() - started
        inputs = client.get("/api/desktop_inputs", query_string={"location": site}).get_json()
        _, lat, lon, tz, _, _ = sites[site]
        dates = [d for c in chunks for d in c["dates"]]
        cases.append({
            "id": f"{site} @ {instant:%Y-%m-%d %H:%M:%S}Z", "site": site, "server": chunks,
            "key_order": key_order, "recorded": rec, "places": places,
            "threshold": inputs["location"]["altitude_threshold"],
            "events": {d: dict(calculate_sun_events_cached(d, tz, lat, lon)) for d in dates},
            "opts": {"inputs": inputs, "library": library, "nowMs": instant.timestamp() * 1000},
        })

    started = time.perf_counter()
    results = _run_node(tmp_path, [{"id": c["id"], "opts": c["opts"]} for c in cases])
    timings["node, all cases"] = time.perf_counter() - started

    tally, problems = {}, []
    for case in cases:
        _compare(case, results[case["id"]], tally, problems)

    print(f"\nheatmap parity: {len(cases)} cases")
    for name, secs in timings.items():
        print(f"  {name}: {secs:.1f} s")
    for cid, t in tally.items():
        print(f"  {cid}: {t['cells']} cells; night edge {len(t['night edge weeks'])} weeks "
              f"{sorted(t['night edge weeks'])}, {t['night edge cells']} cells, max {t['night edge max']:.1f}; "
              f"moon {len(t['moon weeks'])} weeks {sorted(t['moon weeks'])}, {t['moon cells']} cells, "
              f"max {t['moon max']:.1f}; threshold edge {t['threshold edge']}; "
              f"printed rounding {t['printed rounding']}")
        for reason, example in sorted(t["examples"].items()):
            print(f"      e.g. {reason}: {example}")

    # The matrix must reach what it claims to cover
    first = {c["site"]: c for c in cases if c["opts"]["nowMs"] == SUNDAY.timestamp() * 1000}
    london_ids = first["London"]["server"][0]["ids"]
    assert london_ids.index("Tie Zeta") + 1 == london_ids.index("Tie Alpha")
    assert DISABLED not in london_ids and DISABLED in places
    assert any(math.isnan(m) for m in first["London"]["server"][0]["mags"])
    sg = [c for c in cases if c["site"] == "Singapore"]
    assert [c["server"][0]["dates"][0] for c in sg] == ["2026-10-12", "2026-10-19"]
    assert first["Tromso"]["threshold"] == 25.0
    z = lambda c: [v for ch in c["server"] for row in ch["z_chunk"] for v in row]
    assert z(first["London"]) != z(first["London masked"]), "the mask changed nothing"
    assert any(t["night edge weeks"] for t in tally.values()), "no night edge reached"
    assert any(t["moon weeks"] for t in tally.values()), "no Moon rounding week reached"

    for cid, t in tally.items():
        counts = {"night edge weeks": len(t["night edge weeks"]), "moon weeks": len(t["moon weeks"]),
                  "threshold edge": t["threshold edge"], "printed rounding": t["printed rounding"]}
        for name, cap in CAPS.items():
            if counts[name] > cap:
                problems.append(f"{cid}: {counts[name]} {name}, cap {cap}")
    assert not problems, f"{len(problems)} difference(s):\n" + "\n".join(problems[:40])
