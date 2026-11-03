"""Checks for static/js/nova_solar_calendar.js, the Solar System page's Calendar tab.

The unit cases drive the module in node the way the page loads it: plain scripts in one
vm context, in the page's order, so NovaSolarCalendar finds NovaAstro, NovaPlanets,
NovaDashboardRows and NovaSolarRows as globals. The engines are the real ones.

The parity cases rebuild the same events with ephem (a daily scan, then a refinement to
a minute) for 2024-2034 and compare them with the module: the instants, the observing
night each falls in, and that night's maximum altitude, from London and Singapore.
"""

import json
import math
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import ephem
import pytest

REPO = Path(__file__).resolve().parent.parent
STATIC_JS = REPO / "static" / "js"
NODE = shutil.which("node")

PAGE_ORDER = ("nova_astro.js", "nova_transit.js", "nova_dashboard_rows.js",
              "nova_planets.js", "nova_solar_rows.js", "nova_solar_calendar.js")
OPPOSITION_BODIES = ["mars", "jupiter", "saturn", "uranus", "neptune"]
ELONGATION_BODIES = ["mercury", "venus"]
ELONGATION_KINDS = ("elongationEast", "elongationWest")
INNER_KINDS = ("greatestBrilliancy", "dichotomy", "inferiorConjunction", "superiorConjunction")
# The kinds whose elongation is the Sun's and not a pair's: the pair columns take a
# conjunction row, these take a single body
SUN_CONJUNCTION_KINDS = ("inferiorConjunction", "superiorConjunction")
CONJUNCTION_KINDS = ("conjunction",) + SUN_CONJUNCTION_KINDS

LONDON = {"lat": 51.4769, "lon": -0.0005, "timezone": "Europe/London",
          "altitude_threshold": 20, "sampling_interval": 15}
SINGAPORE = {"lat": 1.3521, "lon": 103.8198, "timezone": "Asia/Singapore",
             "altitude_threshold": 20, "sampling_interval": 15}

# Loads the page's scripts into one context, then runs the case's body, which sets
# `result` from `input`; the driver writes `result` as JSON.
_DRIVER = """
'use strict';
const fs = require('fs');
const vm = require('vm');

const [bodyFile, inFile, outFile, ...files] = process.argv.slice(2);
const context = {};
context.self = context;
context.globalThis = context;
vm.createContext(context);
for (const file of files) {
    vm.runInContext(fs.readFileSync(file, 'utf8'), context, { filename: file });
}
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
    run = subprocess.run(
        [NODE, str(driver), str(case), str(inp), str(out)]
        + [str(STATIC_JS / name) for name in PAGE_ORDER],
        capture_output=True, text=True, check=False,
    )
    assert run.returncode == 0, f"node failed ({run.returncode}):\n{run.stderr}"
    return json.loads(out.read_text())


def _loc(base=LONDON, **overrides):
    loc = dict(base)
    loc.update(overrides)
    return {"location": loc}


def _ms(*args, tz="UTC"):
    """UTC milliseconds of a wall time in tz."""
    return int(datetime(*args, tzinfo=ZoneInfo(tz)).timestamp() * 1000)


def _events(tmp_path, inputs, now_ms, sim_date=None):
    return _run(tmp_path, """
        result = NovaSolarCalendar.events({inputs: input.inputs, nowMs: input.nowMs, simDate: input.simDate});
    """, {"inputs": inputs, "nowMs": now_ms, "simDate": sim_date})


# 2026-10-31 20:00 GMT: an evening in London, the night of 2026-10-31.
AUTUMN_EVENING_MS = _ms(2026, 10, 31, 20, 0)


def test_only_planet_events_in_time_order(tmp_path):
    """Oppositions of the outer planets, the inner planets' own events, and conjunctions
    (the only rows with the Moon, checked below); in time order, all inside the window."""
    got = _events(tmp_path, _loc(), AUTUMN_EVENING_MS)
    rows = got["rows"]
    assert got["date"] == "2026-10-31"
    assert [r["utcMs"] for r in rows] == sorted(r["utcMs"] for r in rows)
    for r in rows:
        assert got["window"]["start"] <= r["utcMs"] <= got["window"]["end"]
        if r["kind"] == "conjunction":
            continue
        assert r["bodies"] == [r["body"]]
        if r["body"] in OPPOSITION_BODIES:
            assert r["kind"] == "opposition"
        else:
            assert r["body"] in ELONGATION_BODIES
            assert r["kind"] in ELONGATION_KINDS or r["kind"] in INNER_KINDS
    kinds = {(r["body"], r["kind"]) for r in rows}
    # 26 months hold an opposition of every outer planet and both elongations of each inner one
    assert {(b, "opposition") for b in OPPOSITION_BODIES} <= kinds
    assert {(b, k) for b in ELONGATION_BODIES for k in ELONGATION_KINDS} <= kinds
    # ... and the inner planets' own events, Mercury's the inferior conjunctions only
    assert {("venus", k) for k in INNER_KINDS} <= kinds
    assert ("mercury", "inferiorConjunction") in kinds
    assert not any(r["body"] == "mercury" and r["kind"] == "superiorConjunction" for r in rows)
    assert got["window"]["start"] == _ms(2026, 10, 1, 12, 0, tz="Europe/London")
    assert got["window"]["end"] == _ms(2028, 10, 31, 12, 0, tz="Europe/London")


def test_window_starts_thirty_days_before_the_night(tmp_path):
    """Mars's opposition, 2027-02-19 15:38 UTC, is in the window of the night of
    2027-03-21 (local noon 30 days back is 12:00 on 2027-02-19) and not in the next
    night's. daysFromNow counts nights, so it is -30."""
    def mars(sim):
        got = _events(tmp_path, _loc(), _ms(2026, 11, 1, 20, 0), sim)
        return [r for r in got["rows"] if r["body"] == "mars" and r["kind"] == "opposition"]

    inside = mars("2027-03-21")
    assert len(inside) == 1
    assert inside[0]["night"] == "2027-02-19" and inside[0]["daysFromNow"] == -30
    assert abs(inside[0]["utcMs"] - _ms(2027, 2, 19, 15, 38)) < 3600000
    assert mars("2027-03-22") == []


def test_night_time_and_days_follow_the_location_zone(tmp_path):
    """Each event's night is its local date, or the day before when the local hour is
    before 12; hm and hmDay are its local time and day relative to that night;
    daysFromNow is that night minus the current one."""
    for loc in (LONDON, SINGAPORE):
        got = _events(tmp_path, _loc(loc), AUTUMN_EVENING_MS)
        current = datetime.strptime(got["date"], "%Y-%m-%d").date()
        tz = ZoneInfo(loc["timezone"])
        before_noon = 0
        for r in got["rows"]:
            local = datetime.fromtimestamp(r["utcMs"] / 1000, tz)
            night = local.date() - timedelta(days=1 if local.hour < 12 else 0)
            assert r["night"] == night.isoformat(), r
            assert r["hm"] == local.strftime("%H:%M"), r
            assert r["hmDay"] == (local.date() - night).days
            assert r["daysFromNow"] == (night - current).days
            before_noon += local.hour < 12
        assert before_noon >= 3, "the noon rule decided some rows"


def test_muted_is_max_altitude_below_threshold(tmp_path):
    """muted exactly when that night's max altitude is below the threshold, or for a
    conjunction closer than 15 deg to the Sun (mutedReason 'sun', which wins) — a
    conjunction of an inner planet with the Sun is that same rule; a Moon conjunction by
    its instant instead of the night's maximum (mutedReason 'moment', which wins over
    'sun'); never dropped."""
    counts = []
    for threshold in (-90, 20, 89.9):
        got = _events(tmp_path, _loc(altitude_threshold=threshold), AUTUMN_EVENING_MS)
        assert got["threshold"] == threshold
        for r in got["rows"]:
            sun = r["kind"] in CONJUNCTION_KINDS and r["elongationDeg"] < 15
            if "moon" in r["bodies"]:
                m = r["moment"]
                unseen = m["daylight"] or min(m["alts"]) < threshold
                assert r["muted"] is (unseen or sun)
                assert r["mutedReason"] == ("moment" if unseen else "sun" if sun else None)
                continue
            assert r["moment"] is None and r["occultation"] is False
            assert r["muted"] is (r["maxAlt"] < threshold or sun)
            assert r["mutedReason"] == ("sun" if sun else "altitude" if r["maxAlt"] < threshold else None)
        counts.append((len(got["rows"]), sum(r["muted"] for r in got["rows"]),
                       sum(r["mutedReason"] in ("sun", "moment") for r in got["rows"])))
    assert counts[0][1] == counts[0][2] > 0 and counts[2][1] == counts[2][0]
    assert counts[0][0] == counts[1][0] == counts[2][0]


def test_max_altitude_is_the_bodies_tab_nightly_max(tmp_path):
    """The same night window and sampling as the Bodies tab's month chart: each row's
    maxAlt is NovaSolarRows.nightlyMax() for its night, exactly (for a conjunction the
    higher of its two bodies')."""
    got = _run(tmp_path, """
        const r = NovaSolarCalendar.events({inputs: input.inputs, nowMs: input.nowMs, simDate: null});
        result = r.rows.map(x => {
            const best = x.bodies.map(body => {
                const m = NovaSolarRows.nightlyMax({inputs: input.inputs, body: body,
                    year: +x.night.slice(0, 4), month: +x.night.slice(5, 7)});
                return m.max_alt[m.dates.indexOf(x.night)];
            });
            return [x.maxAlt, Math.max.apply(null, best)];
        });
    """, {"inputs": _loc(SINGAPORE), "nowMs": AUTUMN_EVENING_MS})
    assert len(got) >= 15
    for mine, month_chart in got:
        assert mine == month_chart


def test_simulation_and_the_noon_rule(tmp_path):
    """Simulating today's date at the current wall clock is live; before local noon the
    night is still yesterday's, also in simulation."""
    live = _events(tmp_path, _loc(), AUTUMN_EVENING_MS)
    sim = _events(tmp_path, _loc(), AUTUMN_EVENING_MS, "2026-10-31")
    assert sim == live

    morning = _ms(2026, 11, 1, 11, 0, tz="Europe/London")
    assert _events(tmp_path, _loc(), morning)["date"] == "2026-10-31"
    assert _events(tmp_path, _loc(), morning, "2027-05-10")["date"] == "2027-05-09"


def test_elongation_column_and_event_sides(tmp_path):
    """Mercury and Venus carry the (unsigned) elongation, positive at an eastern and
    negative at a western greatest elongation; the outer planets carry none and are
    within a few degrees of 180 at opposition; the inner planets' other events carry it
    too. At a conjunction with the Sun it is not zero but the planet's own ecliptic
    latitude, the few degrees by which it misses the Sun (8.5 deg at the widest over
    2024-2035, Venus 2033-03-20)."""
    got = _run(tmp_path, """
        const r = NovaSolarCalendar.events({inputs: input.inputs, nowMs: input.nowMs, simDate: null});
        result = r.rows.filter(x => x.kind !== 'conjunction')
            .map(x => [x.body, x.kind, x.elongationDeg, NovaPlanets.details(x.body, x.utcMs).elongationDeg]);
    """, {"inputs": _loc(), "nowMs": AUTUMN_EVENING_MS})
    kinds = {k for _, k, _, _ in got}
    assert set(ELONGATION_KINDS) <= kinds and set(INNER_KINDS) <= kinds, "every kind was compared"
    for body, kind, shown, signed in got:
        if kind == "opposition":
            assert shown is None and abs(signed) > 170, body
            continue
        assert shown == pytest.approx(abs(signed))
        if kind in ELONGATION_KINDS:
            assert (signed > 0) is (kind == "elongationEast"), (body, kind)
            assert (17 if body == "mercury" else 45) < shown < (28 if body == "mercury" else 48)
        elif kind.endswith("Conjunction"):
            assert 0 < shown < 9, (body, kind, shown)
        else:
            assert 20 <= shown < 48, (body, kind, shown)


def test_bad_location_throws(tmp_path):
    got = _run(tmp_path, """
        const tries = [{}, {location: Object.assign({}, input.loc, {lat: null})},
                       {location: Object.assign({}, input.loc, {sampling_interval: 0})}];
        result = tries.map(inputs => {
            try { NovaSolarCalendar.events({inputs: inputs, nowMs: input.nowMs, simDate: null}); return null; }
            catch (e) { return String(e.message); }
        });
    """, {"loc": LONDON, "nowMs": AUTUMN_EVENING_MS})
    assert all(msg and msg.startswith("nova_solar_rows:") for msg in got)


# ------------------------------------------------------------------ ephem parity

PARITY_START = datetime(2024, 1, 1, tzinfo=timezone.utc)
PARITY_END = datetime(2035, 1, 1, tzinfo=timezone.utc)
# Measured worst difference 0.39 h (Uranus); the margin covers the engines' arcminute.
INSTANT_TOLERANCE_H = 1.0
MAX_ALT_TOLERANCE_DEG = 0.1
_EPHEM_EPOCH = ephem.Date("1970/1/1")


def _ephem_ms(d):
    return (float(d) - float(_EPHEM_EPOCH)) * 86400000


def _ephem_events():
    """The events found with ephem: oppositions where the apparent ecliptic longitudes
    of date differ by 180 deg, greatest elongations at the maximum of the apparent
    separation from the Sun; daily scan, then a minute's refinement."""
    sun = ephem.Sun()

    def lon(body, t):
        body.compute(ephem.Date(t))
        return float(ephem.Ecliptic(body, epoch=ephem.Date(t)).lon)

    def from_opposition(body, t):    # planet - Sun - 180 deg, wrapped to -pi..pi
        x = lon(body, t) - lon(sun, t) - math.pi
        return (x + math.pi) % (2 * math.pi) - math.pi

    def separation(body, t):
        body.compute(ephem.Date(t))
        sun.compute(ephem.Date(t))
        return float(ephem.separation(body, sun))

    minute = 1 / 1440
    start, end = float(ephem.Date(PARITY_START)), float(ephem.Date(PARITY_END))
    out = []
    for name in OPPOSITION_BODIES:
        body = getattr(ephem, name.capitalize())()
        t, f0 = start, from_opposition(body, start)
        while t < end:
            f1 = from_opposition(body, t + 1)
            if abs(f0) < 1 and abs(f1) < 1 and (f0 < 0) != (f1 < 0):
                lo, hi = t, t + 1
                while hi - lo > minute:
                    mid = (lo + hi) / 2
                    if (from_opposition(body, mid) < 0) == (f0 < 0):
                        lo = mid
                    else:
                        hi = mid
                out.append((name, "opposition", _ephem_ms((lo + hi) / 2)))
            f0, t = f1, t + 1
    for name in ELONGATION_BODIES:
        body = getattr(ephem, name.capitalize())()
        a, b, t = separation(body, start), separation(body, start + 1), start + 2
        while t < end:
            c = separation(body, t)
            if b > a and b >= c:
                lo, hi = t - 2, t
                while hi - lo > minute:
                    m1, m2 = hi - (hi - lo) * 0.618034, lo + (hi - lo) * 0.618034
                    if separation(body, m1) > separation(body, m2):
                        hi = m2
                    else:
                        lo = m1
                tm = (lo + hi) / 2
                east = (lon(body, tm) - lon(sun, tm)) % (2 * math.pi) < math.pi
                out.append((name, "elongationEast" if east else "elongationWest", _ephem_ms(tm)))
            a, b, t = b, c, t + 1
    return out


@pytest.fixture(scope="module")
def ephem_events():
    return _ephem_events()


def _ephem_max_alt(body_name, times, loc):
    obs = ephem.Observer()
    obs.lat, obs.lon = str(loc["lat"]), str(loc["lon"])
    obs.elevation, obs.pressure = 0, 0
    body = getattr(ephem, body_name.capitalize())()
    best = -90.0
    for t in times:
        obs.date = ephem.Date(datetime.fromtimestamp(t / 1000, timezone.utc))
        body.compute(obs)
        best = max(best, math.degrees(float(body.alt)))
    return best


@pytest.mark.parametrize("loc", [LONDON, SINGAPORE], ids=["london", "singapore"])
def test_events_match_ephem(tmp_path, ephem_events, loc):
    """2024-2034: the same events as ephem, each within an hour; the observing night is
    the one ephem's instant falls in (events within an hour of local noon, where the two
    may straddle the rule, are counted and skipped); that night's maximum altitude
    within 0.1 deg of ephem at the same sample times."""
    got = _run(tmp_path, """
        const found = [];
        for (const body of NovaSolarCalendar.BODIES) {
            for (const e of NovaSolarCalendar.search(body, input.start, input.end)) found.push([body, e.kind, e.utcMs]);
        }
        // Calendars 26 months apart, so every event of the span lies in one of them
        const N = NovaSolarRows._night, A = NovaAstro, W = NovaDashboardRows._wall;
        const s = N.site(input.inputs);
        const rows = [];
        for (const sim of input.sims) {
            for (const r of NovaSolarCalendar.events({inputs: input.inputs, nowMs: input.nowMs, simDate: sim}).rows) {
                const nw = N.nightWindow(r.night, s, A, W, null);
                rows.push({body: r.body, kind: r.kind, utcMs: r.utcMs, night: r.night, maxAlt: r.maxAlt,
                           times: N.samples(nw.start, nw.end, s.interval)});
            }
        }
        result = {found: found, rows: rows};
    """, {"inputs": _loc(loc), "nowMs": AUTUMN_EVENING_MS,
          "start": int(PARITY_START.timestamp() * 1000), "end": int(PARITY_END.timestamp() * 1000),
          "sims": ["2024-01-31", "2026-01-31", "2028-01-31", "2030-01-31", "2032-01-31", "2033-01-31"]})

    found = got["found"]
    assert len(found) == len(ephem_events) >= 120
    tz = ZoneInfo(loc["timezone"])
    near_noon = checked = 0
    for name, kind, ref_ms in ephem_events:
        candidates = [f for f in found if f[0] == name and f[1] == kind and abs(f[2] - ref_ms) < 20 * 86400000]
        assert len(candidates) == 1, (name, kind, ref_ms)
        utc_ms = candidates[0][2]
        assert abs(utc_ms - ref_ms) / 3600000 <= INSTANT_TOLERANCE_H, (name, kind)

        # The calendars scan from other start times, so their refinement may land up to
        # a minute away from search()'s
        row = next(r for r in got["rows"]
                   if r["body"] == name and r["kind"] == kind and abs(r["utcMs"] - utc_ms) <= 120000)
        local = datetime.fromtimestamp(ref_ms / 1000, tz)
        noon = local.replace(hour=12, minute=0, second=0, microsecond=0)
        if abs((local - noon).total_seconds()) < INSTANT_TOLERANCE_H * 3600:
            near_noon += 1
        else:
            night = local.date() - timedelta(days=1 if local.hour < 12 else 0)
            assert row["night"] == night.isoformat(), (name, kind)
        assert abs(row["maxAlt"] - _ephem_max_alt(name, row["times"], loc)) <= MAX_ALT_TOLERANCE_DEG, (name, kind)
        checked += 1
    assert checked == len(ephem_events)
    assert checked - near_noon >= 100, "most nights were compared"


# ------------------------------------------------------------------ conjunctions, closest approach, rings

MOON_PARTNERS = ["venus", "mars", "jupiter", "saturn"]
CONJUNCTION_PLANETS = ["mercury", "venus", "mars", "jupiter", "saturn", "uranus", "neptune"]


def test_conjunction_rows(tmp_path):
    """A conjunction row: one of the 25 pairs, the brighter body first and the click
    target, the separation below the pair's limit (2 deg planets, 1 deg the Moon), the
    smaller of the two elongations; other rows carry no pair."""
    got = _run(tmp_path, """
        const r = NovaSolarCalendar.events({inputs: input.inputs, nowMs: input.nowMs, simDate: null});
        result = {rows: r.rows, el: r.rows.map(x => x.bodies.map(b => NovaPlanets.details(b, x.utcMs).elongationDeg)),
                  pairs: NovaSolarCalendar.PAIRS.length};
    """, {"inputs": _loc(), "nowMs": AUTUMN_EVENING_MS})
    assert got["pairs"] == 21 + 4
    conj = 0
    for r, el in zip(got["rows"], got["el"]):
        if r["kind"] != "conjunction":
            assert r["other"] is None and r["sepDeg"] is None
            continue
        conj += 1
        a, b = r["bodies"]
        assert r["body"] == a and r["other"]["body"] == b
        assert r["mag"] <= r["other"]["mag"]
        if "moon" in r["bodies"]:
            assert a == "moon" and b in MOON_PARTNERS and r["sepDeg"] < 1
        else:
            assert a in CONJUNCTION_PLANETS and b in CONJUNCTION_PLANETS and r["sepDeg"] < 2
        assert r["elongationDeg"] == pytest.approx(min(abs(e) for e in el))
    # Measured for this window: 20 planet pairs under 2 deg, 6 Moon pairs under 1 deg from London
    assert conj >= 20


def test_mars_closest_approach_is_on_the_opposition_row(tmp_path):
    """Not a row of its own: the 2027 opposition row (night of 2027-02-19) carries the
    closest approach, 2027-02-20 00:09 UTC (still the night of the 19th in London),
    0.67787 AU = 101.4 million km. No other row has one."""
    got = _events(tmp_path, _loc(), AUTUMN_EVENING_MS)
    assert all(r["kind"] != "closestApproach" for r in got["rows"])
    carrying = [r for r in got["rows"] if r["closestApproach"]]
    assert [(r["body"], r["kind"], r["night"]) for r in carrying] == [("mars", "opposition", "2027-02-19")]
    ca = carrying[0]["closestApproach"]
    assert ca["night"] == "2027-02-19"
    assert abs(ca["utcMs"] - _ms(2027, 2, 20, 0, 9)) < 3600000
    assert ca["distAU"] * 149.5978707 == pytest.approx(101.4, abs=0.05)


def test_ring_tilt_on_saturn_rows(tmp_path):
    """Every row with Saturn in it carries Saturn's ring tilt at the instant; no other row."""
    got = _run(tmp_path, """
        const r = NovaSolarCalendar.events({inputs: input.inputs, nowMs: input.nowMs, simDate: null});
        result = r.rows.map(x => [x.bodies, x.ringTiltDeg, NovaPlanets.details('saturn', x.utcMs).ringTiltDeg]);
    """, {"inputs": _loc(), "nowMs": AUTUMN_EVENING_MS})
    with_saturn = 0
    for bodies, tilt, engine in got:
        if "saturn" in bodies:
            with_saturn += 1
            assert tilt == engine
        else:
            assert tilt is None
    assert with_saturn >= 3


def _london_observer(utc_ms):
    obs = ephem.Observer()
    obs.lat, obs.lon = str(LONDON["lat"]), str(LONDON["lon"])
    obs.pressure = 0          # geometric altitudes, as NovaPlanets.altAz
    obs.date = datetime.fromtimestamp(utc_ms / 1000, tz=timezone.utc).replace(tzinfo=None)
    return obs


def _moon_row(rows, partner, night):
    found = [r for r in rows if r["bodies"] == ["moon", partner] and r["night"] == night]
    assert len(found) == 1, [(r["bodies"], r["night"]) for r in rows if "moon" in r["bodies"]]
    return found[0]


def test_moon_conjunction_in_daylight_is_muted(tmp_path):
    """London, Moon-Jupiter on the night of 2026-10-05: closest at 11:17 BST the next
    morning, 0.5 deg apart and both near 48 deg up, but in daylight (the night's maximum
    would have said visible). Muted, mutedReason 'moment', moment.daylight."""
    row = _moon_row(_events(tmp_path, _loc(), AUTUMN_EVENING_MS)["rows"], "jupiter", "2026-10-05")
    assert (row["hm"], row["hmDay"]) == ("11:17", 1)
    assert row["maxAlt"] >= 20
    assert row["muted"] is True and row["mutedReason"] == "moment"
    assert row["moment"]["daylight"] is True and min(row["moment"]["alts"]) >= 20
    assert row["occultation"] is False
    sun = ephem.Sun(_london_observer(row["utcMs"]))
    assert math.degrees(sun.alt) > 20


def test_moon_occultation_below_the_threshold_is_muted(tmp_path):
    """London, Moon-Venus on the night of 2028-03-29: closest at 05:31 BST before
    sunrise, but both bodies 13 deg below the horizon. Muted, mutedReason 'moment', not
    daylight, with both altitudes (Moon first). The separation, 0.02 deg, is inside the
    Moon's apparent radius: an occultation, as ephem's topocentric places agree."""
    row = _moon_row(_events(tmp_path, _loc(), AUTUMN_EVENING_MS)["rows"], "venus", "2028-03-29")
    assert (row["hm"], row["hmDay"]) == ("05:31", 1)
    assert row["maxAlt"] >= 20
    assert row["muted"] is True and row["mutedReason"] == "moment"
    assert row["moment"]["daylight"] is False
    moon_alt, venus_alt = row["moment"]["alts"]
    assert moon_alt == pytest.approx(-13.0, abs=0.5) and venus_alt == pytest.approx(-13.0, abs=0.5)
    assert row["occultation"] is True
    assert row["sepDeg"] * 3600 < row["diameterArcsec"] / 2

    obs = _london_observer(row["utcMs"])
    sun, moon, venus = ephem.Sun(obs), ephem.Moon(obs), ephem.Venus(obs)
    assert math.degrees(sun.alt) < -1
    assert math.degrees(moon.alt) == pytest.approx(moon_alt, abs=0.5)
    assert math.degrees(venus.alt) == pytest.approx(venus_alt, abs=0.5)
    assert math.degrees(ephem.separation(moon, venus)) < math.degrees(moon.radius)


def test_occultation_only_for_the_moon_inside_its_radius(tmp_path):
    """occultation is true exactly for Moon conjunctions closer than the Moon's apparent
    radius; planet pairs never; moment only on Moon rows."""
    rows = _events(tmp_path, _loc(), AUTUMN_EVENING_MS)["rows"]
    for r in rows:
        moon = r["kind"] == "conjunction" and "moon" in r["bodies"]
        assert (r["moment"] is not None) is moon
        assert r["occultation"] is (moon and r["sepDeg"] * 3600 < r["diameterArcsec"] / 2)
    assert any(r["occultation"] for r in rows)
    assert any(r["kind"] == "conjunction" and "moon" in r["bodies"] and not r["occultation"] for r in rows)


def test_moon_conjunction_in_the_dark_and_high_is_visible(tmp_path):
    """London, Moon-Mars on the night of 2026-10-04: 06:23 BST, before sunrise, both
    about 50 deg up. Not muted."""
    row = _moon_row(_events(tmp_path, _loc(), AUTUMN_EVENING_MS)["rows"], "mars", "2026-10-04")
    assert row["muted"] is False and row["mutedReason"] is None
    assert row["moment"]["daylight"] is False and min(row["moment"]["alts"]) >= 20


def _ephem_minima(f, start, end, step):
    """Local minima of f over ephem dates, a scan every `step` days, then golden section
    to a minute: [(utc ms, value)]."""
    out = []
    a, b, t = f(start), f(start + step), start + 2 * step
    while t <= end:
        c = f(t)
        if b < a and b <= c:
            lo, hi = t - 2 * step, t
            while hi - lo > 1 / 1440:
                m1, m2 = hi - (hi - lo) * 0.618034, lo + (hi - lo) * 0.618034
                if f(m1) < f(m2):
                    hi = m2
                else:
                    lo = m1
            tm = (lo + hi) / 2
            out.append((_ephem_ms(tm), f(tm)))
        a, b, t = b, c, t + step
    return out


def _parity_span():
    return float(ephem.Date(PARITY_START)), float(ephem.Date(PARITY_END))


def _span_ms():
    return {"start": int(PARITY_START.timestamp() * 1000), "end": int(PARITY_END.timestamp() * 1000)}


def test_mars_closest_approach_matches_ephem(tmp_path):
    """2024-2034: the minima of Mars's distance as ephem's earth_distance has them, each
    within 2 h (measured worst 1.0 h; the minimum is flat) and 2e-4 AU (measured 1.0e-4)."""
    mars = ephem.Mars()

    def dist(t):
        mars.compute(ephem.Date(t))
        return mars.earth_distance

    ref = _ephem_minima(dist, *_parity_span(), 1)
    got = _run(tmp_path, "result = NovaSolarCalendar.closestApproaches(input.start, input.end).map(e => [e.utcMs, e.distAU]);",
               _span_ms())
    assert len(got) == len(ref) == 5
    for (ref_ms, ref_au), (utc_ms, au) in zip(ref, got):
        assert abs(utc_ms - ref_ms) <= 2 * 3600000
        assert abs(au - ref_au) <= 2e-4


INNER = {"mercury", "venus", "mars"}


def _match(ref, got, tol_h, tol_deg):
    """Each event (a, b, utc ms, separation) of ref has exactly one in got for the same
    pair within five days, within tol_h(a, b) hours and tol_deg."""
    for a, b, ref_ms, ref_sep in ref:
        found = [g for g in got if g[0] == a and g[1] == b and abs(g[2] - ref_ms) < 5 * 86400000]
        assert len(found) == 1, (a, b, ref_ms, ref_sep)
        assert abs(found[0][2] - ref_ms) / 3600000 <= tol_h(a, b), (a, b, ref_ms)
        assert abs(found[0][3] - ref_sep) <= tol_deg, (a, b, ref_ms)


def test_planet_conjunctions_match_ephem(tmp_path):
    """2024-2034, geocentric: the minima of the separation of ephem's astrometric places
    (a_ra, a_dec), the same both ways (the engine's under 2.4 deg among ephem's under
    2.5 and the other way round, so the limit's edge cannot decide). Within 0.03 deg
    (measured 0.018) and 1.5 h for a pair with Mercury, Venus or Mars (measured 0.84 h);
    18 h for two outer planets, whose minimum is nearly flat (measured 10.9 h,
    Saturn-Neptune 2025)."""
    start, end = _parity_span()
    bodies = {p: getattr(ephem, p.capitalize())() for p in CONJUNCTION_PLANETS}
    ref = []
    for i, a in enumerate(CONJUNCTION_PLANETS):
        for b in CONJUNCTION_PLANETS[i + 1:]:
            A, B = bodies[a], bodies[b]

            def sep(t, A=A, B=B):
                d = ephem.Date(t)
                A.compute(d)
                B.compute(d)
                return math.degrees(float(ephem.separation((A.a_ra, A.a_dec), (B.a_ra, B.a_dec))))

            ref += [(a, b, ms, v) for ms, v in _ephem_minima(sep, start, end, 1) if v < 2.5]
    got = _run(tmp_path, """
        const out = [];
        for (const p of NovaSolarCalendar.PAIRS) {
            if (p[0] === 'moon') continue;
            for (const e of NovaSolarCalendar.searchPair(p[0], p[1], input.start, input.end, {limitDeg: 2.5})) {
                out.push([p[0], p[1], e.utcMs, e.sepDeg]);
            }
        }
        result = out;
    """, _span_ms())
    assert len(ref) >= 100
    tol = lambda a, b: 1.5 if (a in INNER or b in INNER) else 18.0
    _match([e for e in ref if e[3] < 2.4], got, tol, 0.03)
    _match([e for e in got if e[3] < 2.4], ref, tol, 0.03)


@pytest.mark.parametrize("loc", [LONDON, SINGAPORE], ids=["london", "singapore"])
def test_moon_conjunctions_match_ephem(tmp_path, loc):
    """2024-2034, topocentric: the minima of the separation of the Moon and each partner
    as ephem places them for the observer (elevation 0, pressure 0: no refraction), the
    same both ways (under 1.2 deg among under 1.3). Within 0.25 h (measured 0.06 h) and
    0.01 deg (measured 0.004)."""
    start, end = _parity_span()
    obs = ephem.Observer()
    obs.lat, obs.lon = str(loc["lat"]), str(loc["lon"])
    obs.elevation, obs.pressure = 0, 0
    moon = ephem.Moon()
    ref = []
    for p in MOON_PARTNERS:
        B = getattr(ephem, p.capitalize())()

        def sep(t, B=B):
            obs.date = ephem.Date(t)
            moon.compute(obs)
            B.compute(obs)
            return math.degrees(float(ephem.separation((moon.ra, moon.dec), (B.ra, B.dec))))

        ref += [("moon", p, ms, v) for ms, v in _ephem_minima(sep, start, end, 0.125) if v < 1.3]
    got = _run(tmp_path, """
        const out = [];
        for (const p of NovaSolarCalendar.PAIRS) {
            if (p[0] !== 'moon') continue;
            for (const e of NovaSolarCalendar.searchPair(p[0], p[1], input.start, input.end,
                                                         {lat: input.lat, lon: input.lon, limitDeg: 1.3})) {
                out.push([p[0], p[1], e.utcMs, e.sepDeg]);
            }
        }
        result = out;
    """, dict(_span_ms(), lat=loc["lat"], lon=loc["lon"]))
    assert len(ref) >= 50
    tol = lambda a, b: 0.25
    _match([e for e in ref if e[3] < 1.2], got, tol, 0.01)
    _match([e for e in got if e[3] < 1.2], ref, tol, 0.01)


# ------------------------------------- the inner planets' greatest brilliancy and phase

# Venus's magnitude is flat to ephem's own 0.01 mag for a week or more around greatest
# brilliancy (the plateau runs 1-10 days over 2024-2035), so ephem cannot date the peak
# better than the middle of that plateau, and the two are within 0.62 d of each other.
# The margin is the plateau, not the agreement.
BRILLIANCY_TOLERANCE_DAYS = 5
# The reference takes the lowest magnitude over this many days either side.
BRILLIANCY_WINDOW_DAYS = 20
# ephem's magnitude is rounded to 0.01, so this asks for the plateau's lowest step.
BRILLIANCY_MAG_TOLERANCE = 0.005
# The window the two are matched in, days either side.
MATCH_DAYS = 20

_SUN = ephem.Sun()


def _ecliptic_lon(body, t):
    return float(ephem.Ecliptic(body, epoch=ephem.Date(t)).lon)


def _elongation(body, t):
    """The signed difference of the apparent ecliptic longitudes of date, wrapped to
    -pi..pi: the same side rule as the engine's elongationDeg (its sign only has to be
    consistent for the wrapping not to matter at these elongations)."""
    body.compute(ephem.Date(t))
    _SUN.compute(ephem.Date(t))
    d = _ecliptic_lon(body, t) - _ecliptic_lon(_SUN, t)
    return (d + math.pi) % (2 * math.pi) - math.pi


def _phase_angle_deg(body, t):
    body.compute(ephem.Date(t))
    k = float(body.phase) / 100.0
    return math.degrees(math.acos(max(-1.0, min(1.0, 2 * k - 1))))


def _crossings(f, start, end, step=1.0):
    """The instants where f changes sign, sampled every step, narrowed to a minute."""
    out, t = [], start
    a = f(t)
    while t < end:
        b = f(t + step)
        if (a < 0) != (b < 0):
            lo, hi = t, t + step
            while hi - lo > 1 / 1440:
                mid = (lo + hi) / 2
                if (f(mid) < 0) == (a < 0):
                    lo = mid
                else:
                    hi = mid
            out.append((lo + hi) / 2)
        a, t = b, t + step
    return out


def _ephem_inner(guard):
    """The inner planets' events as ephem has them, over the parity span.
    brilliancy: the middle of each plateau of Venus's magnitude that is the lowest over
    BRILLIANCY_WINDOW_DAYS either side, with `guard`'s phase-angle and elongation
    clauses applied, with the plateau's lowest magnitude. dichotomy: Venus's phase
    crossing 50%. conjunctions: the elongation crossing zero, inferior when the planet
    is the nearer of the two at that instant."""
    start, end = _parity_span()
    venus = ephem.Venus()
    out = {"brilliancy": [], "dichotomy": [], "conjunctions": []}

    def mag(t):
        venus.compute(ephem.Date(t))
        return float(venus.mag)

    plateau = []
    t = start
    while t <= end:
        if (_phase_angle_deg(venus, t) >= guard["phase"]
                and abs(math.degrees(_elongation(venus, t))) >= guard["elong"]):
            lowest = min(mag(t + x) for x in range(-BRILLIANCY_WINDOW_DAYS, BRILLIANCY_WINDOW_DAYS + 1))
            if mag(t) <= lowest + BRILLIANCY_MAG_TOLERANCE:
                plateau.append((t, mag(t)))
        t += 1.0
    groups = []
    for day, m in plateau:
        if groups and day - groups[-1][-1][0] <= 2 * BRILLIANCY_WINDOW_DAYS:
            groups[-1].append((day, m))
        else:
            groups.append([(day, m)])
    for g in groups:
        out["brilliancy"].append({
            "ms": _ephem_ms((g[0][0] + g[-1][0]) / 2),
            "mag": min(m for _, m in g),
            "days": len(g),
        })

    def half_lit(t):
        venus.compute(ephem.Date(t))
        return float(venus.phase) - 50.0

    for t in _crossings(half_lit, start, end):
        out["dichotomy"].append(_ephem_ms(t))

    for name, ctor in (("venus", ephem.Venus), ("mercury", ephem.Mercury)):
        body = ctor()
        for t in _crossings(lambda t, body=body: _elongation(body, t), start, end):
            body.compute(ephem.Date(t))
            _SUN.compute(ephem.Date(t))
            nearer = float(body.earth_distance) < float(_SUN.earth_distance)
            out["conjunctions"].append({
                "body": name,
                "kind": "inferiorConjunction" if nearer else "superiorConjunction",
                "ms": _ephem_ms(t),
            })
    return out


@pytest.fixture(scope="module")
def brilliancy_guard(tmp_path_factory):
    """The engine's own guard constants, so the reference cannot drift from them."""
    tmp = tmp_path_factory.mktemp("guard")
    return _run(tmp, """
        result = {phase: NovaSolarCalendar.BRILLIANCY_MIN_PHASE_DEG,
                  elong: NovaSolarCalendar.BRILLIANCY_MIN_ELONG_DEG,
                  k: NovaSolarCalendar.DICHOTOMY_K};
    """, {})


@pytest.fixture(scope="module")
def ephem_inner(brilliancy_guard):
    return _ephem_inner(brilliancy_guard)


@pytest.fixture(scope="module")
def engine_inner(tmp_path_factory):
    """Every inner event both ways, with the sign of the elongation at its instant."""
    tmp = tmp_path_factory.mktemp("inner")
    return _run(tmp, """
        const out = [];
        for (const body of NovaSolarCalendar.ELONGATION_BODIES) {
            for (const e of NovaSolarCalendar.searchInner(body, input.start, input.end)) {
                out.push([body, e.kind, e.utcMs, Math.sign(NovaPlanets.details(body, e.utcMs).elongationDeg)]);
            }
        }
        result = out;
    """, _span_ms())


def _paired(ref_ms, got_ms):
    """The one `got` instant for a reference one, or None: they are a synodic period
    apart at the nearest, so MATCH_DAYS cannot pick the wrong cycle."""
    near = [u for u in got_ms if abs(u - ref_ms) <= MATCH_DAYS * 86400000]
    assert len(near) <= 1, (ref_ms, near)
    return near[0] if near else None


def test_inner_conjunctions_match_ephem(ephem_inner, engine_inner):
    """2024-2035: the same conjunctions with the Sun as ephem has them, each within an
    hour (measured worst 0.27 h). The engine decides inferior from superior by the
    direction of the elongation crossing, this reference by ephem's distances, so a
    swapped rule fails. Mercury's superior conjunctions are the ones left out on purpose:
    they are the only reference events with no row, and there are 35 of them."""
    ref = ephem_inner["conjunctions"]
    got = [(b, k, u) for b, k, u, _ in engine_inner if k in SUN_CONJUNCTION_KINDS]
    omitted = [c for c in ref if c["body"] == "mercury" and c["kind"] == "superiorConjunction"]
    assert len(omitted) >= 30
    wanted = [c for c in ref if c not in omitted]
    assert len(wanted) == len(got) >= 45, "the same events, the omitted ones aside"

    by_kind = {}
    for body, kind, ms in got:
        by_kind.setdefault((body, kind), []).append(ms)
    paired = []
    for c in wanted:
        ms = _paired(c["ms"], by_kind.get((c["body"], c["kind"]), []))
        assert ms is not None, (c["body"], c["kind"], c["ms"])
        assert abs(ms - c["ms"]) / 3600000 <= INSTANT_TOLERANCE_H, (c["body"], c["kind"])
        paired.append(ms)
    # the match is one to one, so nothing the engine found is left over
    assert sorted(paired) == sorted(u for _, _, u in got) != []
    assert not any(b == "mercury" and k == "superiorConjunction" for b, k, _ in got)


def test_dichotomy_matches_ephem(ephem_inner, engine_inner):
    """2024-2035: Venus's illuminated fraction crossing one half, as ephem's phase has
    it, each within an hour (measured worst 0.52 h). Two a synodic period, and the side
    alternates: the one before inferior conjunction is the evening one (east of the Sun),
    the one after it the morning one."""
    ref = ephem_inner["dichotomy"]
    got = [(u, s) for b, k, u, s in engine_inner if k == "dichotomy"]
    inward = sorted(u for b, k, u, _ in engine_inner if k == "inferiorConjunction" and b == "venus")
    assert len(ref) == len(got) >= 12
    for r in ref:
        ms = _paired(r, [u for u, _ in got])
        assert ms is not None and abs(ms - r) / 3600000 <= INSTANT_TOLERANCE_H, r
    sides = [s for u, s in sorted(got)]
    assert all(a != b for a, b in zip(sides, sides[1:])), sides
    for u, s in got:
        nearest = min((abs(c - u), c) for c in inward)[1]
        assert s == (1 if nearest > u else -1), (u, s, nearest)


def test_greatest_brilliancy_matches_ephem(ephem_inner, engine_inner):
    """2024-2035: the same greatest brilliancies as ephem has them — within
    BRILLIANCY_TOLERANCE_DAYS of the middle of ephem's plateau (measured worst 0.62 d)
    and at a magnitude ephem calls the plateau's lowest (measured: exactly, all 14)."""
    venus = ephem.Venus()
    ref = ephem_inner["brilliancy"]
    got = [u for b, k, u, _ in engine_inner if k == "greatestBrilliancy"]
    assert len(ref) == len(got) >= 12
    for b in ref:
        ms = _paired(b["ms"], got)
        assert ms is not None, b["ms"]
        assert abs(ms - b["ms"]) / 86400000 <= BRILLIANCY_TOLERANCE_DAYS, (b["ms"], ms)
        venus.compute(ephem.Date(ms / 86400000 + _EPHEM_EPOCH))
        assert float(venus.mag) <= b["mag"] + BRILLIANCY_MAG_TOLERANCE, (b["ms"], ms)


# Venus's three false magnitude minima of 2026-2029 and the guard clauses each falls
# foul of: the thin crescent is past the half-lit point and is the elongation's alone to
# reject, the two near superior conjunction fail both.
FALSE_PLATEAUS = [
    ("2026-10-25", ["elong"]),
    ("2027-07-19", ["phase", "elong"]),
    ("2027-08-30", ["phase", "elong"]),
]


def test_the_false_brilliancy_plateaus_produce_no_row(tmp_path, brilliancy_guard):
    """Venus's magnitude curve has other shallow minima that are not greatest
    brilliancy: one on the way to inferior conjunction, as a thin crescent, and one
    either side of superior conjunction, where the near-full disc turns over. Three fall
    in 2026-2029. The guard rejects each — the crescent by its elongation alone, the
    other two by both clauses — so none is a row, and the minima the guard keeps in that
    span are exactly the five real ones."""
    got = _run(tmp_path, """
        const out = [];
        for (const e of NovaSolarCalendar.searchInner('venus', input.start, input.end)) {
            if (e.kind === 'greatestBrilliancy') out.push(e.utcMs);
        }
        result = {rows: out, details: input.dates.map(d => {
            const x = NovaPlanets.details('venus', Date.parse(d + 'T12:00:00Z'));
            return [x.phaseAngleDeg, Math.abs(x.elongationDeg)];
        })};
    """, {"start": _ms(2026, 1, 1), "end": _ms(2030, 1, 1),
          "dates": [d for d, _ in FALSE_PLATEAUS]})

    guard = [brilliancy_guard["phase"], brilliancy_guard["elong"]]
    for (date, clause), (phase, elong) in zip(FALSE_PLATEAUS, got["details"]):
        rejected = []
        if phase < guard[0]:
            rejected.append("phase")
        if elong < guard[1]:
            rejected.append("elong")
        assert rejected == clause, (date, phase, elong, rejected)
        ms = _ms(*[int(x) for x in date.split("-")], 12, 0)
        assert not [u for u in got["rows"] if abs(u - ms) <= 30 * 86400000], date

    # ... and the minima the guard keeps over 2026-2029 are the five real ones, with a
    # real least-brightness magnitude each
    venus = ephem.Venus()
    assert len(got["rows"]) == 5
    for u in got["rows"]:
        venus.compute(ephem.Date(u / 86400000 + _EPHEM_EPOCH))
        assert -4.6 < float(venus.mag) < -4.3, u


def test_inner_event_rows(tmp_path):
    """A row for each of them through the whole calendar: the same single-body shape as
    an opposition or an elongation (no pair, no Moon fields), the event's magnitude and
    diameter, and the muting a conjunction with the Sun deserves — the Sun rule, not the
    night's altitude, even when the planet is under the horizon anyway. Venus's greatest
    brilliancy and its dichotomies are ordinary rows, muted by altitude alone, and its
    magnitude at a greatest brilliancy is the -4.5 it is famous for."""
    got = _events(tmp_path, _loc(), AUTUMN_EVENING_MS)
    rows = [r for r in got["rows"] if r["kind"] in INNER_KINDS]
    kinds = {r["kind"] for r in rows}
    assert kinds == set(INNER_KINDS), "every kind is a row of this window"
    assert {r["body"] for r in rows if r["kind"] in INNER_KINDS} == {"mercury", "venus"}
    for r in rows:
        assert r["bodies"] == [r["body"]]
        assert r["sepDeg"] is None and r["other"] is None, r
        assert r["moment"] is None and r["occultation"] is False, r
        assert r["closestApproach"] is None and r["ringTiltDeg"] is None, r
        if r["kind"] in SUN_CONJUNCTION_KINDS:
            assert 0 < r["elongationDeg"] < 9, r
            assert r["muted"] is True and r["mutedReason"] == "sun", r
        else:
            assert r["muted"] is (r["maxAlt"] < got["threshold"]), r
            assert r["mutedReason"] == ("altitude" if r["muted"] else None), r
            if r["kind"] == "greatestBrilliancy":
                assert -4.6 < r["mag"] < -4.3, r
                assert 20 <= r["elongationDeg"] < 45, r
                assert 35 < r["diameterArcsec"] < 45, r
            else:
                assert 45 <= r["elongationDeg"] < 48, r
                assert 20 < r["diameterArcsec"] < 30, r
    assert any(not r["muted"] for r in rows), "some of them are visible tonight"
