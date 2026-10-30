"""Unit checks for static/js/nova_solar_rows.js, the Solar System page's bodies table.

Each test drives the module in node the way the page loads it: plain scripts in one vm
context, in the page's order, so NovaSolarRows finds NovaAstro, NovaPlanets and
NovaDashboardRows as globals. The engines are the real ones; every case states the rule
it checks and is built so that rule decides the outcome.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
STATIC_JS = REPO / "static" / "js"
NODE = shutil.which("node")

PAGE_ORDER = ("nova_astro.js", "nova_transit.js", "nova_dashboard_rows.js",
              "nova_planets.js", "nova_solar_rows.js")
BODIES = ["mercury", "venus", "mars", "jupiter", "saturn", "uranus", "neptune", "moon"]

LONDON = {"lat": 51.4769, "lon": -0.0005, "timezone": "Europe/London",
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


def _loc(**overrides):
    loc = dict(LONDON)
    loc.update(overrides)
    return {"location": loc}


# 2026-07-15 21:00 UTC is 22:00 BST: an evening on the summer (DST) side.
SUMMER_EVENING_MS = 1784149200000


def test_every_body_in_order_even_when_none_qualifies(tmp_path):
    """All eight, in BODIES order, whatever the threshold: nothing is filtered out."""
    got = _run(tmp_path, """
        const r = NovaSolarRows.rows({inputs: input.inputs, nowMs: input.nowMs, simDate: null});
        result = { bodies: r.rows.map(x => x.body), muted: r.rows.map(x => x.muted),
                   engine: Array.from(NovaPlanets.BODIES) };
    """, {"inputs": _loc(altitude_threshold=89.9), "nowMs": SUMMER_EVENING_MS})
    assert got["bodies"] == BODIES == got["engine"]
    assert all(got["muted"]), "no body reaches 89.9 deg from London"


def test_muted_is_max_altitude_below_threshold(tmp_path):
    """muted exactly when tonight's max altitude is below the threshold."""
    got = _run(tmp_path, """
        result = [-90, 20, 89.9].map(th => {
            const inputs = JSON.parse(JSON.stringify(input.inputs));
            inputs.location.altitude_threshold = th;
            const r = NovaSolarRows.rows({inputs: inputs, nowMs: input.nowMs, simDate: null});
            return r.rows.map(x => [x.maxAlt, x.muted, r.threshold]);
        });
    """, {"inputs": _loc(), "nowMs": SUMMER_EVENING_MS})
    for per_threshold in got:
        for max_alt, muted, threshold in per_threshold:
            assert muted is (max_alt < threshold)
    assert not any(m for _, m, _ in got[0])


def test_times_are_the_location_wall_clock(tmp_path):
    """Rise, transit and set print as HH:MM in the location's zone (BST in July), with
    their day relative to the night's date; the transit lies inside its interval."""
    got = _run(tmp_path, """
        const r = NovaSolarRows.rows({inputs: input.inputs, nowMs: input.nowMs, simDate: null});
        result = { date: r.date, night: r.night, rows: r.rows };
    """, {"inputs": _loc(), "nowMs": SUMMER_EVENING_MS})
    assert got["date"] == "2026-07-15"

    def bst(utc_ms):
        minutes = utc_ms // 60000 + 60  # UTC+1, truncated to the minute
        day = (minutes // 1440) - (1784073600000 // 60000 // 1440)  # days after 2026-07-15
        minutes %= 1440
        return f"{minutes // 60:02d}:{minutes % 60:02d}", day

    checked = overlapping = 0
    for row in got["rows"]:
        for key in ("rise", "set", "transit"):
            if row[key + "Utc"] is not None:
                assert (row[key], row[key + "Day"]) == bst(row[key + "Utc"]), f"{row['body']} {key}"
                checked += 1
        if row["riseUtc"] is not None and row["setUtc"] is not None:
            assert row["riseUtc"] < row["transitUtc"] < row["setUtc"], row["body"]
            # the interval shown is always tonight's: it overlaps sunset to sunrise
            assert row["riseUtc"] < got["night"]["end"] and row["setUtc"] > got["night"]["start"], row["body"]
            overlapping += 1
    assert checked >= 20
    assert overlapping >= 5
    # Mercury that night sets before sunset and rises after sunrise.
    mercury = got["rows"][0]
    assert mercury["notUpTonight"] is True
    assert mercury["riseUtc"] is None and mercury["setUtc"] is None and mercury["transitUtc"] is not None


# Greenwich, 2026-10-30: Venus is a morning planet (west of the Sun), rising before dawn.
AUTUMN_EVENING_MS = 1793394000000      # 2026-10-30 21:00 GMT
# 2026-11-04: the Moon, past full, rises after midnight.
NOVEMBER_EVENING_MS = 1793826000000    # 2026-11-04 21:00 GMT
# 2026-07-30: Venus is an evening planet, set before astronomical dusk.
VENUS_EVENING_MS = 1785441600000       # 2026-07-30 21:00 BST


def _body(tmp_path, inputs, now_ms, body):
    return _run(tmp_path, """
        const r = NovaSolarRows.rows({inputs: input.inputs, nowMs: input.nowMs, simDate: null});
        const loc = input.inputs.location;
        const next = new Date(Date.parse(r.date + 'T00:00:00Z') + 86400000).toISOString().slice(0, 10);
        result = { date: r.date, night: r.night, row: r.rows.find(x => x.body === input.body),
                   midnight: NovaAstro.localToUtc(+next.slice(0, 4), +next.slice(5, 7), +next.slice(8, 10), 0, 0, loc.timezone) };
        const x = result.row;
        result.labels = ['rise', 'transit', 'set'].map(k => NovaSolarRows.timeLabel(x[k], x[k + 'Day']));
    """, {"inputs": inputs, "nowMs": now_ms, "body": body})


def test_morning_planet_rises_after_midnight(tmp_path):
    """Venus west of the Sun: the interval shown is the one rising before sunrise, so all
    three times are the next calendar day and carry +1."""
    got = _body(tmp_path, _loc(), AUTUMN_EVENING_MS, "venus")
    row, night = got["row"], got["night"]
    assert row["elongationSide"] == "W"
    assert got["midnight"] < row["riseUtc"] < night["end"], "rises between midnight and sunrise"
    assert row["setUtc"] > night["end"]
    assert (row["riseDay"], row["transitDay"], row["setDay"]) == (1, 1, 1)
    assert all(label.endswith(" +1") for label in got["labels"])


def test_evening_planet_sets_before_midnight(tmp_path):
    """Venus east of the Sun: risen before sunset, set before midnight, all on the night's
    own date, so no suffix."""
    got = _body(tmp_path, _loc(altitude_threshold=10), VENUS_EVENING_MS, "venus")
    row, night = got["row"], got["night"]
    assert row["elongationSide"] == "E"
    assert row["riseUtc"] < night["start"], "risen before sunset"
    assert night["start"] < row["setUtc"] < got["midnight"], "set after sunset, before midnight"
    assert (row["riseDay"], row["transitDay"], row["setDay"]) == (0, 0, 0)
    assert got["labels"] == [row["rise"], row["transit"], row["set"]]


def test_moon_rising_after_midnight(tmp_path):
    """The waning Moon: tonight's interval is the one rising after midnight, not the
    previous one, which set during the day."""
    got = _body(tmp_path, _loc(), NOVEMBER_EVENING_MS, "moon")
    row, night = got["row"], got["night"]
    assert got["date"] == "2026-11-04"
    assert got["midnight"] < row["riseUtc"] < night["end"]
    assert row["riseDay"] == 1 and row["rise"] == "02:34"
    assert row["riseUtc"] < row["transitUtc"] < row["setUtc"]
    assert got["labels"][0] == "02:34 +1"


def test_day_suffixes(tmp_path):
    """+1 for the next calendar day, −1 (U+2212) for the day before the night's date.
    Tromsø, 2026-10-27: the Moon rose the afternoon before and is up all night, so the
    interval holding the night's midpoint starts on the 26th and transits on the 28th."""
    tromso = {"location": {"lat": 69.6492, "lon": 18.9553, "timezone": "Europe/Oslo",
                           "altitude_threshold": 20, "sampling_interval": 15}}
    got = _body(tmp_path, tromso, 1793131200000, "moon")  # 2026-10-27 21:00 CET
    row, night = got["row"], got["night"]
    mid = (night["start"] + night["end"]) / 2
    assert got["date"] == "2026-10-27"
    assert row["riseUtc"] < night["start"] and (row["setUtc"] is None or row["setUtc"] > night["end"])
    assert row["riseUtc"] <= mid
    assert (row["riseDay"], row["transitDay"]) == (-1, 1)
    assert got["labels"][0] == row["rise"] + " \u22121"
    assert got["labels"][1] == row["transit"] + " +1"
    labels = _run(tmp_path, """
        result = [NovaSolarRows.timeLabel('03:12', 0), NovaSolarRows.timeLabel('03:12', 1),
                  NovaSolarRows.timeLabel('03:12', -1), NovaSolarRows.timeLabel(null, 1)];
    """, {})
    assert labels == ["03:12", "03:12 +1", "03:12 \u22121", None]


def test_month_series_is_the_nightly_max_venus_evening(tmp_path):
    """The month chart's value per day is the highest altitude from that day's sunset to the
    next sunrise. Venus in the July 2026 evening sky is above the horizon after every sunset,
    although at local midnight it is below it every day."""
    got = _run(tmp_path, """
        const loc = input.inputs.location;
        const m = NovaSolarRows.nightlyMax({inputs: input.inputs, body: 'venus', year: 2026, month: 7});
        const midnight = m.dates.map(d => {
            const t = new Date(Date.parse(d + 'T00:00:00Z') + 86400000).toISOString().slice(0, 10);
            return NovaPlanets.altAz('venus', NovaAstro.localToUtc(+t.slice(0, 4), +t.slice(5, 7), +t.slice(8, 10), 0, 0, loc.timezone), loc.lat, loc.lon).alt;
        });
        result = { dates: m.dates, max: m.max_alt, midnight: midnight };
    """, {"inputs": _loc()})
    assert len(got["dates"]) == 31 and got["dates"][0] == "2026-07-01"
    assert all(v > 0 for v in got["max"]), got["max"]
    assert all(v < 0 for v in got["midnight"]), "Venus is set by midnight all month"


def test_transit_carries_its_altitude(tmp_path):
    """The transit's altitude is the body's at that instant, below the horizon too."""
    got = _run(tmp_path, """
        const r = NovaSolarRows.rows({inputs: input.inputs, nowMs: input.nowMs, simDate: null});
        const loc = input.inputs.location;
        result = r.rows.filter(x => x.transitUtc !== null).map(x =>
            [x.body, x.transitAlt, NovaPlanets.altAz(x.body, x.transitUtc, loc.lat, loc.lon).alt]);
    """, {"inputs": _loc(), "nowMs": SUMMER_EVENING_MS})
    assert len(got) == len(BODIES)
    for body, transit_alt, alt_then in got:
        assert abs(transit_alt - alt_then) < 0.05, body


def test_default_selection_is_the_highest_max_altitude(tmp_path):
    got = _run(tmp_path, """
        const r = NovaSolarRows.rows({inputs: input.inputs, nowMs: input.nowMs, simDate: null});
        result = { maxAlt: r.rows.map(x => [x.body, x.maxAlt]),
                   def: NovaSolarRows.defaultBody(r.rows),
                   saturn: NovaSolarRows.pickBody(r.rows, 'saturn'),
                   pluto: NovaSolarRows.pickBody(r.rows, 'pluto'),
                   none: NovaSolarRows.pickBody(r.rows, null) };
    """, {"inputs": _loc(), "nowMs": SUMMER_EVENING_MS})
    best = max(got["maxAlt"], key=lambda kv: kv[1])[0]
    assert got["def"] == best
    assert got["saturn"] == "saturn"
    assert got["pluto"] == best and got["none"] == best


def test_live_observing_night_follows_the_noon_rule(tmp_path):
    """Live: before local noon the night is the day before, from noon on it is the day."""
    got = _run(tmp_path, """
        result = input.clocks.map(ms => NovaSolarRows.rows({inputs: input.inputs, nowMs: ms, simDate: null}).date);
    """, {"inputs": _loc(),
          # 2026-07-15 10:59:59.500 UTC is 11:59:59.5 BST; 11:00:00.500 UTC is 12:00:00.5 BST.
          "clocks": [1784113199500, 1784113200500]})
    assert got == ["2026-07-14", "2026-07-15"]


def test_simulation_uses_the_dashboard_table_night(tmp_path):
    """Simulated: the sim date at the current wall clock, then the same noon rule as the
    dashboard's rows() (not statusStrip(), which takes the sim date as given)."""
    got = _run(tmp_path, """
        result = input.clocks.map(ms => {
            const r = NovaSolarRows.rows({inputs: input.inputs, nowMs: ms, simDate: '2026-03-10'});
            const strip = NovaDashboardRows.statusStrip({inputs: input.inputs, nowMs: ms, simDate: '2026-03-10'});
            return [r.date, r.instantUtc, strip.date];
        });
    """, {"inputs": _loc(),
          # 09:00 and 20:00 UTC on 2026-07-15; London is on BST, so 10:00 and 21:00 wall.
          "clocks": [1784106000000, 1784145600000]})
    morning, evening = got
    # 10:00 wall on 2026-03-10 (GMT, so 10:00 UTC): before noon, the night of the 9th.
    assert morning == ["2026-03-09", 1773136800000, "2026-03-10"]
    assert evening == ["2026-03-10", 1773176400000, "2026-03-10"]


def test_elongation_is_unsigned_with_its_side(tmp_path):
    got = _run(tmp_path, """
        const r = NovaSolarRows.rows({inputs: input.inputs, nowMs: input.nowMs, simDate: null});
        result = r.rows.map(x => [x.body, x.elongationDeg, x.elongationSide,
                                  NovaPlanets.details(x.body, r.instantUtc).elongationDeg]);
    """, {"inputs": _loc(), "nowMs": SUMMER_EVENING_MS})
    sides = set()
    for body, elong, side, signed in got:
        assert elong == abs(signed), body
        assert side == ("E" if signed >= 0 else "W"), body
        sides.add(side)
    assert sides == {"E", "W"}, "the case should cover both sides"


def test_venus_in_evening_twilight_is_not_muted(tmp_path):
    """The night for max altitude is sunset to sunrise, not the astronomical night: Venus
    east of the Sun, above the threshold after sunset but set before astronomical dusk,
    is not muted, although the dark window alone would mute it."""
    got = _run(tmp_path, """
        const loc = input.inputs.location;
        const r = NovaSolarRows.rows({inputs: input.inputs, nowMs: input.nowMs, simDate: null});
        const venus = r.rows.find(x => x.body === 'venus');
        const sun = NovaAstro.sunEvents(r.date, loc.timezone, loc.lat, loc.lon);
        const next = NovaAstro.sunEvents('2026-07-31', loc.timezone, loc.lat, loc.lon);
        // The dashboard's dark window for the same night: astronomical dusk to dawn.
        const dark = NovaDashboardRows._wall.nightWindow(r.date, loc.timezone, 15, sun, NovaAstro);
        const darkCurve = NovaPlanets.altAzCurve('venus', Array.from(dark.times), loc.lat, loc.lon);
        result = { date: r.date, night: r.night, sunset: sun.sunset_utc, dusk: sun.astronomical_dusk_utc,
                   sunrise: next.sunrise_utc, venus: venus, darkMax: Math.max(...darkCurve.alt) };
    """, {"inputs": _loc(altitude_threshold=10),
          # 2026-07-30 21:00 BST
          "nowMs": 1785441600000})
    venus = got["venus"]
    assert got["date"] == "2026-07-30"
    assert got["night"] == {"start": got["sunset"], "end": got["sunrise"], "kind": "sun"}
    assert venus["elongationSide"] == "E"
    assert got["sunset"] < venus["setUtc"] < got["dusk"], "Venus must set in twilight"
    assert got["darkMax"] < 10, "the astronomical night alone would mute Venus"
    assert venus["maxAlt"] >= 10
    assert venus["muted"] is False


def test_not_up_tonight(tmp_path):
    """Tromsø, 2026-09-15: the Moon rises and sets within the search window but is below
    the horizon from sunset to sunrise. No rise or set from another day is shown: the row
    is notUpTonight, not neverUp, and keeps the transit closest to the night's midpoint,
    with its altitude and day."""
    tromso = {"location": {"lat": 69.6492, "lon": 18.9553, "timezone": "Europe/Oslo",
                           "altitude_threshold": 20, "sampling_interval": 15}}
    got = _run(tmp_path, """
        const loc = input.inputs.location;
        const r = NovaSolarRows.rows({inputs: input.inputs, nowMs: input.nowMs, simDate: null});
        const x = r.rows.find(y => y.body === 'moon');
        const found = NovaPlanets.events('moon', r.searchWindow.start, r.searchWindow.end, loc.lat, loc.lon);
        const mid = (r.night.start + r.night.end) / 2;
        const transits = found.events.filter(e => e.kind === 'transit').map(e => e.utcMs);
        result = { date: r.date, row: x, kinds: found.events.map(e => e.kind), mid: mid, transits: transits,
                   label: NovaSolarRows.timeLabel(x.transit, x.transitDay) };
    """, {"inputs": tromso, "nowMs": 1789502400000})  # 2026-09-15 22:00 CEST
    row = got["row"]
    assert got["date"] == "2026-09-15"
    assert "rise" in got["kinds"] and "set" in got["kinds"], "it does rise and set around this night"
    assert row["notUpTonight"] is True
    assert row["neverUp"] is False and row["alwaysUp"] is False
    assert row["rise"] is None and row["set"] is None
    assert row["maxAlt"] < -34 / 60, "below the rise/set altitude all night"
    assert row["muted"] is True
    closest = min(got["transits"], key=lambda t: abs(t - got["mid"]))
    assert row["transitUtc"] == closest
    assert row["transitAlt"] is not None and row["transitDay"] is not None
    assert got["label"].startswith(row["transit"])


# Longyearbyen, Svalbard: polar night in December, midnight sun in June.
SVALBARD = {"location": {"lat": 78.2, "lon": 15.6, "timezone": "Arctic/Longyearbyen",
                         "altitude_threshold": 20, "sampling_interval": 15}}
SVALBARD_DECEMBER_MS = 1797804000000  # 2026-12-20 22:00 UTC, 23:00 CET
SVALBARD_JUNE_MS = 1782079200000      # 2026-06-21 22:00 UTC, 00:00 CEST on 06-22


def test_always_up_circumpolar_body(tmp_path):
    """Svalbard, 2026-12-20: Uranus (dec about +20) never sets at 78.2 N. A body with no
    rise or set in the whole search window is alwaysUp, with no rise or set, not an
    interval without ends; its transit is still the one closest to the night's middle."""
    got = _run(tmp_path, """
        const loc = input.inputs.location;
        const r = NovaSolarRows.rows({inputs: input.inputs, nowMs: input.nowMs, simDate: null});
        const found = NovaPlanets.events('uranus', r.searchWindow.start, r.searchWindow.end, loc.lat, loc.lon);
        result = { date: r.date, row: r.rows.find(y => y.body === 'uranus'),
                   kinds: found.events.map(e => e.kind), alwaysUp: found.alwaysUp };
    """, {"inputs": SVALBARD, "nowMs": SVALBARD_DECEMBER_MS})
    row = got["row"]
    assert got["date"] == "2026-12-20"
    assert got["alwaysUp"] is True and set(got["kinds"]) <= {"transit"}, "circumpolar in the engine"
    assert row["alwaysUp"] is True
    assert row["neverUp"] is False and row["notUpTonight"] is False
    assert row["rise"] is None and row["set"] is None
    assert row["riseDay"] is None and row["setDay"] is None
    assert row["transit"] is not None and row["transitAlt"] > 0
    assert row["maxAlt"] > 0


def test_polar_night_window_is_noon_to_noon(tmp_path):
    """Svalbard in December: the Sun does not rise, so the night window is local noon on
    the night's date (12:00 CET, 11:00 UTC) to local noon the day after."""
    got = _run(tmp_path, """
        const r = NovaSolarRows.rows({inputs: input.inputs, nowMs: input.nowMs, simDate: null});
        result = { date: r.date, night: r.night };
    """, {"inputs": SVALBARD, "nowMs": SVALBARD_DECEMBER_MS})
    assert got["date"] == "2026-12-20"
    assert got["night"]["kind"] == "noon"
    assert got["night"]["start"] == 1797764400000  # 2026-12-20 11:00 UTC
    assert got["night"]["end"] == 1797850800000    # 2026-12-21 11:00 UTC


def test_polar_day_window_is_noon_to_noon(tmp_path):
    """Svalbard in June: the Sun does not set. At 00:00 CEST on 06-22 the observing night
    is 06-21 (before noon: the evening before), noon CEST (10:00 UTC) to noon."""
    got = _run(tmp_path, """
        const r = NovaSolarRows.rows({inputs: input.inputs, nowMs: input.nowMs, simDate: null});
        result = { date: r.date, night: r.night, bodies: r.rows.length };
    """, {"inputs": SVALBARD, "nowMs": SVALBARD_JUNE_MS})
    assert got["date"] == "2026-06-21"
    assert got["night"]["kind"] == "noon"
    assert got["night"]["start"] == 1782036000000  # 2026-06-21 10:00 UTC
    assert got["night"]["end"] == 1782122400000    # 2026-06-22 10:00 UTC
    assert got["bodies"] == len(BODIES)


def test_month_series_year_bounds(tmp_path):
    """nightlyMax() takes 1900-2100, the page's year input bounds: Date.UTC would read a
    year below 100 as 1900 + year, so year 50 must throw rather than plot 1950."""
    got = _run(tmp_path, """
        result = [1899, 1900, 2100, 2101, 50].map(year => {
            try {
                const s = NovaSolarRows.nightlyMax({inputs: input.inputs, body: 'mars', year: year, month: 2});
                return { year: year, ok: true, first: s.dates[0], days: s.dates.length };
            } catch (e) {
                return { year: year, ok: false, error: String(e.message) };
            }
        });
    """, {"inputs": _loc(sampling_interval=60)})
    by_year = {r["year"]: r for r in got}
    assert by_year[1900] == {"year": 1900, "ok": True, "first": "1900-02-01", "days": 28}
    assert by_year[2100] == {"year": 2100, "ok": True, "first": "2100-02-01", "days": 28}
    for year in (1899, 2101, 50):
        assert by_year[year]["ok"] is False
        assert "year out of range" in by_year[year]["error"]
