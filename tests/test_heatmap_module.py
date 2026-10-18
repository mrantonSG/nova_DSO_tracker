"""Unit checks for static/js/nova_heatmap.js, the browser's yearly heatmap.

Each test drives the module in node the way the page will load it — plain scripts
in one vm context, the three files in order — and asserts a rule
get_yearly_heatmap_chunk() holds to. The expected values are worked out here in
Python with the route's own expressions (pytz for the week start and the Moon
instant, strftime for the labels, float() and round() for the numbers), so a test
fails where the module and Python disagree.

Some cases wrap NovaAstro rather than fake it: the wrapper delegates to the real
engine and records the calls, and where a test needs it pins sunEvents,
moonPhaseAt or observableDuration, so the module's own week grid, ordering,
rounding and chunk building still run. No server comparison here: that is
tests/test_heatmap_parity.py.
"""

import json
import math
import shutil
import subprocess
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest
import pytz

REPO = Path(__file__).resolve().parent.parent
STATIC_JS = REPO / "static" / "js"
NODE = shutil.which("node")
ENGINE_FILES = ("nova_astro.js", "nova_dashboard_rows.js", "nova_heatmap.js")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

ROUTE_KEYS = ["chunk_index", "x", "z_chunk", "y", "moon_phases", "ids", "active", "dates",
              "types", "cons", "mags", "sizes", "sbs"]

# Loads the three files the way the page does and runs each case. A stub, when
# present, replaces NovaAstro with a copy of itself that records moonPhaseAt and
# observableDuration calls and, where the stub says so, returns fixed values.
# Non-finite numbers and -0 are sent back as {"num": "..."} so JSON keeps them.
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
    const calls = { moon: [], duration: [] };
    const st = c.stub || {};
    const s = Object.assign(Object.create(null), real);
    s.moonPhaseAt = ms => { calls.moon.push(ms); return st.phase === undefined ? real.moonPhaseAt(ms) : st.phase; };
    if (st.events) s.sunEvents = () => Object.assign({}, st.events);
    s.observableDuration = (ra, dec, lat, lon, date, tz, o) => {
        const r = st.duration ? Object.assign({ from: null, to: null }, st.duration)
                              : real.observableDuration(ra, dec, lat, lon, date, tz, o);
        calls.duration.push({ date: date, ra: ra, threshold: o.threshold, interval: o.interval,
                              mask: o.mask, windowInterval: o.window && o.window.interval,
                              minutes: r.minutes, maxAltitude: r.maxAltitude });
        return r;
    };
    context.NovaAstro = s;
    try {
        if (c.call === 'pyFloat') return { values: c.values.map(v => context.NovaHeatmap._pyFloat(v)) };
        const H = context.NovaHeatmap;
        let chunks;
        if (c.call === 'split') {
            const ctx = H.prepare(c.opts);
            chunks = [];
            for (let i = 0; i < 12; i++) chunks.push(H.chunk(ctx, i));
            return { chunks: chunks, whole: H.chunks(c.opts), calls: calls };
        }
        chunks = c.only === undefined ? H.chunks(c.opts) : [H.chunk(H.prepare(c.opts), c.only)];
        return { chunks: chunks, keys: chunks.map(k => Object.keys(k)), calls: calls };
    } catch (e) {
        return { error: String((e && e.message) || e) };
    } finally {
        context.NovaAstro = real;
    }
}

const encode = (k, v) => (typeof v === 'number' && (!isFinite(v) || Object.is(v, -0)))
    ? { num: Object.is(v, -0) ? '-0' : String(v) } : v;
const cases = JSON.parse(fs.readFileSync(inFile, 'utf8'));
fs.writeFileSync(outFile, JSON.stringify(cases.map(c => Object.assign({ id: c.id }, runOne(c))), encode));
"""


def _decode(v):
    if isinstance(v, dict) and set(v) == {"num"}:
        return {"NaN": math.nan, "Infinity": math.inf, "-Infinity": -math.inf, "-0": -0.0}[v["num"]]
    if isinstance(v, dict):
        return {k: _decode(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_decode(x) for x in v]
    return v


def _run(tmp_path, cases):
    (tmp_path / "heatmap_driver.js").write_text(_DRIVER)
    (tmp_path / "heatmap_cases.json").write_text(json.dumps(cases))
    run = subprocess.run(
        [NODE, str(tmp_path / "heatmap_driver.js"),
         *[str(STATIC_JS / f) for f in ENGINE_FILES],
         str(tmp_path / "heatmap_cases.json"), str(tmp_path / "heatmap_out.json")],
        capture_output=True, text=True, check=False,
    )
    assert run.returncode == 0, f"node failed ({run.returncode}):\n{run.stderr}"
    return {r["id"]: _decode(r) for r in json.loads((tmp_path / "heatmap_out.json").read_text())}


LONDON = {"lat": 51.509865, "lon": -0.118092, "timezone": "Europe/London"}
SINGAPORE = {"lat": 1.3521, "lon": 103.8198, "timezone": "Asia/Singapore"}
NOW = 1792341000000  # 2026-10-18 16:30 UTC: Sunday in London, Monday 00:30 in Singapore


def _loc(site=LONDON, **over):
    loc = dict(site, altitude_threshold=20, sampling_interval=15, horizon_mask=[])
    loc.update(over)
    return loc


def _obj(name, ra=6.0, dec=40.0, enabled=True, active=False):
    return {"Object": name, "ra": ra, "dec": dec, "enabled": enabled, "ActiveProject": active}


def _row(name, **over):
    row = {"Object": name, "Common Name": name + " common", "Type": "Neb", "Constellation": "Ori",
           "Magnitude": "8.5", "Size": "10", "SB": "21.5"}
    row.update(over)
    return row


def _case(cid, objects, rows=None, loc=None, now=NOW, stub=None, call=None, only=None):
    if rows is None:
        rows = sorted((_row(o["Object"]) for o in objects if o["enabled"]), key=lambda r: r["Object"])
    case = {"id": cid, "opts": {"inputs": {"location": loc or _loc(), "objects": objects,
                                           "settings": {}, "skyglow": None},
                                "library": {"objects": rows}, "nowMs": now}}
    if stub is not None:
        case["stub"] = stub
    if call is not None:
        case["call"] = call
    if only is not None:
        case["only"] = only
    return case


def _route_weeks(now_ms, tz_name):
    """The route's start_date_year, dates and labels for all 52 weeks."""
    tz = pytz.timezone(tz_name)
    now = datetime.fromtimestamp(now_ms / 1000, pytz.utc).astimezone(tz)
    start = now.date() - timedelta(days=now.weekday())
    days = [start + timedelta(weeks=i) for i in range(52)]
    return start, days


def _route_chunk_ranges():
    per, rem = 52 // 12, 52 % 12
    out = []
    for i in range(12):
        s = i * per + min(i, rem)
        out.append((s, s + per + (1 if i < rem else 0)))
    return out


def _route_score(max_alt, minutes, moon, thr):
    """The route's score expression, rounded as it rounds it."""
    if max_alt is None or max_alt < thr or minutes < 45:
        score = 0
    else:
        norm_alt = min((max_alt - thr) / (90 - thr), 1.0)
        norm_dur = min(minutes / 480, 1.0)
        score = (0.4 * norm_alt + 0.6 * norm_dur) * 100
        if moon > 60:
            penalty_factor = ((moon - 60) / 40) * 0.9
            score *= (1 - penalty_factor)
    return round(score, 1)


def _route_float(v, fallback):
    try:
        return float(v)
    except:  # noqa: E722 - the route's bare except
        return fallback


def _same(a, b):
    if isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b):
        return True
    return a == b and (not isinstance(a, float) or math.copysign(1, a) == math.copysign(1, b))


# --- the week grid ------------------------------------------------------------------

def test_chunks_hold_5_then_4_weeks_with_the_routes_dates_and_labels(tmp_path):
    r = _run(tmp_path, [_case("x", [_obj("A")])])["x"]
    chunks = r["chunks"]
    assert [c["chunk_index"] for c in chunks] == list(range(12))
    assert [len(c["dates"]) for c in chunks] == [5, 5, 5, 5, 4, 4, 4, 4, 4, 4, 4, 4]

    start, days = _route_weeks(NOW, "Europe/London")
    for c, (s, e) in zip(chunks, _route_chunk_ranges()):
        assert c["dates"] == [d.strftime("%Y-%m-%d") for d in days[s:e]]
        assert c["x"] == [d.strftime("%b %d") for d in days[s:e]]
        assert len(c["x"]) == len(c["moon_phases"]) == len(c["z_chunk"][0])
    assert start == date(2026, 10, 12) and days[-1] == date(2027, 10, 4)


def test_chunk_has_exactly_the_routes_keys_in_order(tmp_path):
    r = _run(tmp_path, [_case("x", [_obj("A")])])["x"]
    assert all(keys == ROUTE_KEYS for keys in r["keys"])


def test_prepare_then_chunk_equals_chunks(tmp_path):
    r = _run(tmp_path, [_case("x", [_obj("A"), _obj("B", ra=12.0)], call="split")])["x"]
    assert r["chunks"] == r["whole"]


@pytest.mark.parametrize("site, expected", [(LONDON, date(2026, 10, 12)), (SINGAPORE, date(2026, 10, 19))])
def test_week_starts_on_the_monday_at_the_location(tmp_path, site, expected):
    """16:30 UTC on Sunday 2026-10-18 is still Sunday in London but already Monday
    00:30 in Singapore, so the two grids start a week apart."""
    r = _run(tmp_path, [_case("x", [_obj("A", dec=10.0)], loc=_loc(site))])["x"]
    start, _ = _route_weeks(NOW, site["timezone"])
    assert start == expected
    assert r["chunks"][0]["dates"][0] == expected.strftime("%Y-%m-%d")


def test_week_start_at_a_monday_just_after_midnight_and_just_before(tmp_path):
    sg = pytz.timezone("Asia/Singapore")
    before = int(sg.localize(datetime(2026, 10, 18, 23, 59)).timestamp() * 1000)
    after = int(sg.localize(datetime(2026, 10, 19, 0, 0)).timestamp() * 1000)
    loc = _loc(SINGAPORE)
    r = _run(tmp_path, [_case("before", [_obj("A", dec=10.0)], loc=loc, now=before),
                        _case("after", [_obj("A", dec=10.0)], loc=loc, now=after)])
    assert r["before"]["chunks"][0]["dates"][0] == "2026-10-12"
    assert r["after"]["chunks"][0]["dates"][0] == "2026-10-19"


# --- the Moon -----------------------------------------------------------------------

def test_moon_is_read_at_local_midnight_after_each_monday(tmp_path):
    """London changes back to GMT on Sunday 2026-10-25; the Tuesday after it is the
    first instant read at +00:00."""
    r = _run(tmp_path, [_case("x", [_obj("A")], stub={})])["x"]
    tz = pytz.timezone("Europe/London")
    _, days = _route_weeks(NOW, "Europe/London")
    expected = [int(tz.localize(datetime.combine(d + timedelta(days=1), time.min))
                    .astimezone(pytz.utc).timestamp() * 1000) for d in days]
    assert r["calls"]["moon"] == expected


def test_moon_midnight_that_does_not_exist_is_read_as_pytz_reads_it(tmp_path):
    """Tehran moved to +04:30 at 00:00 on Tuesday 2022-03-22, so the Moon instant of
    the week starting Monday 2022-03-21 is a wall time that does not exist. pytz's
    localize() (is_dst=False) reads it at the old offset, +03:30. The only DST change
    at a Tuesday's midnight in any common zone 2015-2027: the Moon is read on
    Tuesdays, and the changes at midnight elsewhere (America/Santiago, Africa/Cairo)
    fall on other days."""
    tz = pytz.timezone("Asia/Tehran")
    now = int(tz.localize(datetime(2022, 3, 21, 12, 0)).timestamp() * 1000)
    loc = {"lat": 35.6892, "lon": 51.3890, "timezone": "Asia/Tehran"}
    r = _run(tmp_path, [_case("x", [_obj("A")], loc=_loc(loc), now=now, stub={})])["x"]
    gap = tz.localize(datetime(2022, 3, 22, 0, 0))
    assert gap.utcoffset() == timedelta(hours=3, minutes=30)
    assert r["calls"]["moon"][0] == int(gap.astimezone(pytz.utc).timestamp() * 1000)


@pytest.mark.parametrize("phase", [12.25, 0.35, 60.05, 99.95, 70.0])
def test_moon_phase_is_rounded_like_python(tmp_path, phase):
    """12.25 is a binary tie (Python rounds to even: 12.2); 0.35 and 60.05 lie just
    below their halves in binary (0.3, 60.0)."""
    r = _run(tmp_path, [_case("x", [_obj("A")], stub={"phase": phase})])["x"]
    phases = [p for c in r["chunks"] for p in c["moon_phases"]]
    assert phases == [round(phase, 1)] * 52


# --- the objects --------------------------------------------------------------------

def test_disabled_unplaced_and_geometrically_hidden_objects_have_no_row(tmp_path):
    # London lat 51.509865, threshold 20: dec -18.490135 is exactly 20 deg at transit
    edge = 51.509865 - 70
    objects = [_obj("Kept"), _obj("Off", enabled=False), _obj("NoRa", ra=None),
               _obj("NoDec", dec=None), _obj("Edge", dec=edge), _obj("Low", dec=edge - 0.001)]
    r = _run(tmp_path, [_case("x", objects)])["x"]
    assert 90 - abs(51.509865 - edge) >= 20 and not 90 - abs(51.509865 - (edge - 0.001)) >= 20
    assert r["chunks"][0]["ids"] == ["Kept", "Edge"]


def test_ra_sort_is_stable_and_ties_keep_payload_order(tmp_path):
    """Payload (id) order Zeta, Alpha at the same RA; the library comes in name order,
    as /api/desktop_library sends it."""
    objects = [_obj("Late", ra=20.0), _obj("Zeta Tie", ra=6.0), _obj("Early", ra=1.0),
               _obj("Alpha Tie", ra=6.0)]
    r = _run(tmp_path, [_case("x", objects)])["x"]
    assert r["chunks"][0]["ids"] == ["Early", "Zeta Tie", "Alpha Tie", "Late"]
    assert all(c["ids"] == r["chunks"][0]["ids"] for c in r["chunks"])


def test_labels_and_text_columns(tmp_path):
    objects = [_obj("A", ra=1.0), _obj("B", ra=2.0, active=True), _obj("C", ra=3.0), _obj("D", ra=4.0)]
    rows = [_row("A"),
            _row("B", **{"Common Name": None, "Type": None, "Constellation": None}),
            _row("C", **{"Common Name": "", "Type": ""}),
            _row("D", **{"Common Name": "Dumbbell", "Type": "PN", "Constellation": "Vul"})]
    c = _run(tmp_path, [_case("x", objects, rows=rows)])["x"]["chunks"][0]

    def route_label(row):
        name = row["Common Name"] or row["Object"]
        if row["Type"]:
            name += f" [{row['Type']}]"
        return name

    assert c["y"] == [route_label(r) for r in rows] == ["A common [Neb]", "B", "C", "Dumbbell [PN]"]
    assert c["types"] == [str(r["Type"] or "") for r in rows]
    assert c["cons"] == [str(r["Constellation"] or "") for r in rows]
    assert c["active"] == [0, 1, 0, 0]


FLOAT_CASES = [" 8.5 ", "1_0", "nan", None, "N/A", "-inf", "Infinity", "+NaN", "1__0", "_1", "1_",
               "1_.5", "1e1_0", ".5", "5.", ".", "1e", "\x1c5", "\x855", "\xa05\u3000", "\ufeff5",
               "\u0663.\u0665", "\U0001d7d3", "0x10", "1e400", "-0", "\uff11\uff12", "8.5 mag", "",
               "  ", "inF", "infinit", "1.5E-3", "\t\n 7 \r\v\f", "20.82"]


def test_python_float_semantics(tmp_path):
    r = _run(tmp_path, [{"id": "x", "call": "pyFloat", "values": FLOAT_CASES}])["x"]
    for raw, got in zip(FLOAT_CASES, r["values"]):
        want = _route_float(raw, None)
        assert _same(got, want) if want is not None else got is None, (raw, got, want)


def test_mags_sizes_sbs_use_float_with_the_routes_fallbacks(tmp_path):
    values = [" 8.5 ", "1_0", "nan", None, "N/A", "-inf", "1e400"]
    objects = [_obj(f"O{i}", ra=float(i)) for i in range(len(values))]
    rows = [_row(f"O{i}", Magnitude=v, Size=v, SB=v) for i, v in enumerate(values)]
    c = _run(tmp_path, [_case("x", objects, rows=rows)])["x"]["chunks"][0]
    for got, want in ((c["mags"], 999.0), (c["sizes"], 0.0), (c["sbs"], 999.0)):
        expected = [_route_float(v, want) for v in values]
        assert all(_same(float(g), e) for g, e in zip(got, expected)), (got, expected)


# --- the score ----------------------------------------------------------------------

@pytest.mark.parametrize("max_alt, minutes, phase", [
    (55.0, 240, 10.0),     # 50.0, no Moon penalty
    (55.0, 240, 80.0),     # penalty 0.45
    (55.0, 240, 60.0),     # 60 itself: no penalty
    (19.999, 600, 0.0),    # below the threshold
    (20.0, 60, 0.0),       # at the threshold: kept, altitude term 0
    (70.0, 44, 0.0),       # under 45 minutes
    (70.0, 45, 0.0),       # 45 minutes: kept
    (95.0, 900, 100.0),    # both terms capped, full Moon
    (33.3, 123, 61.7),     # an uneven case for the rounding
    (20.0, 98, 0.0),       # 12.25, a binary tie: Python rounds to even (12.2), Math.round up
])
def test_score_matches_the_route(tmp_path, max_alt, minutes, phase):
    stub = {"phase": phase, "duration": {"minutes": minutes, "maxAltitude": max_alt}}
    r = _run(tmp_path, [_case("x", [_obj("A")], stub=stub)])["x"]
    z = [v for c in r["chunks"] for v in c["z_chunk"][0]]
    assert z == [_route_score(max_alt, minutes, round(phase, 1), 20)] * 52


def test_threshold_comes_from_the_location_and_interval_is_60(tmp_path):
    loc = _loc(altitude_threshold=35, sampling_interval=15)
    r = _run(tmp_path, [_case("x", [_obj("A", dec=50.0)], loc=loc, stub={})])["x"]
    calls = r["calls"]["duration"]
    assert len(calls) == 52
    assert {c["threshold"] for c in calls} == {35}
    assert {c["interval"] for c in calls} == {60} and {c["windowInterval"] for c in calls} == {60}


def test_week_without_a_night_window_scores_zero(tmp_path):
    na = {"astronomical_dusk": "N/A", "astronomical_dawn": "N/A", "sunset": "N/A", "sunrise": "N/A"}
    r = _run(tmp_path, [_case("x", [_obj("A")], stub={"events": na})])["x"]
    assert [v for c in r["chunks"] for v in c["z_chunk"][0]] == [0] * 52
    assert r["calls"]["duration"] == []


def test_horizon_mask_cuts_duration_but_not_max_altitude(tmp_path):
    """A 60-degree wall to the south: M42-like object at London, real engine."""
    wall = [[90.0, 60.0], [270.0, 60.0]]
    obj = [_obj("A", ra=5.58, dec=-5.4)]
    r = _run(tmp_path, [_case("open", obj, stub={}),
                        _case("masked", obj, loc=_loc(horizon_mask=wall), stub={})])
    open_calls, masked_calls = r["open"]["calls"]["duration"], r["masked"]["calls"]["duration"]
    assert all(c["mask"] is None for c in open_calls)
    assert all(c["mask"] == wall for c in masked_calls)
    assert [c["maxAltitude"] for c in open_calls] == [c["maxAltitude"] for c in masked_calls]
    assert any(o["minutes"] > m["minutes"] for o, m in zip(open_calls, masked_calls))
    assert all(o["minutes"] >= m["minutes"] for o, m in zip(open_calls, masked_calls))


def test_single_point_horizon_mask_is_no_mask(tmp_path):
    """The route only applies a mask with more than one point."""
    obj = [_obj("A", ra=5.58, dec=-5.4)]
    r = _run(tmp_path, [_case("open", obj, stub={}),
                        _case("one", obj, loc=_loc(horizon_mask=[[180.0, 60.0]]), stub={})])
    assert all(c["mask"] is None for c in r["one"]["calls"]["duration"])
    assert r["one"]["chunks"] == r["open"]["chunks"]


# --- what makes it throw ------------------------------------------------------------

def test_throws_where_the_route_would_do_something_else(tmp_path):
    objs = [_obj("A"), _obj("B")]
    good = _case("good", objs)
    cases = [good]

    def broken(cid, edit):
        c = json.loads(json.dumps(good))
        c["id"] = cid
        edit(c["opts"])
        cases.append(c)

    broken("no_location", lambda o: o["inputs"].update(location=None))
    broken("no_inputs", lambda o: o.pop("inputs"))
    broken("no_library", lambda o: o.pop("library"))
    broken("library_not_list", lambda o: o["library"].update(objects={"A": {}}))
    broken("missing_in_library", lambda o: o["library"]["objects"].pop())
    broken("extra_in_library", lambda o: o["library"]["objects"].append(_row("Z")))
    broken("no_enabled_flag", lambda o: o["inputs"]["objects"][0].pop("enabled"))
    broken("lat_none", lambda o: o["inputs"]["location"].update(lat=None))
    broken("no_timezone", lambda o: o["inputs"]["location"].pop("timezone"))
    broken("bad_timezone", lambda o: o["inputs"]["location"].update(timezone="Mars/Olympus"))
    broken("no_threshold", lambda o: o["inputs"]["location"].pop("altitude_threshold"))
    broken("bad_mask", lambda o: o["inputs"]["location"].update(horizon_mask=[[90, "x"], [180, 10]]))
    broken("ra_text", lambda o: o["inputs"]["objects"][0].update(ra="6h"))
    broken("no_now", lambda o: o.pop("nowMs"))

    r = _run(tmp_path, cases)
    assert "chunks" in r["good"]
    for c in cases[1:]:
        assert "error" in r[c["id"]], c["id"]


def test_chunk_index_out_of_range_throws(tmp_path):
    r = _run(tmp_path, [_case("x", [_obj("A")], only=12)])
    assert "error" in r["x"]
