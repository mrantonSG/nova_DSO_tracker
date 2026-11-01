"""Checks for static/js/nova_solar_seeing.js, the Solar System page's seeing forecast.

The module runs in node as the page loads it, after the engines it looks up at call
time (nova_astro, nova_transit, nova_dashboard_rows, nova_planets, nova_solar_rows).
The rating rules are checked on plain numbers; forecast() over real nights, with wind
series built here so each case can tell which hours were used.
"""

import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
STATIC_JS = REPO / "static" / "js"
NODE = shutil.which("node")

FILES = ("nova_astro.js", "nova_transit.js", "nova_dashboard_rows.js", "nova_planets.js",
         "nova_solar_rows.js", "nova_solar_seeing.js")

LONDON = {"name": "Home", "lat": 51.4769, "lon": -0.0005, "timezone": "Europe/London",
          "altitude_threshold": 20, "sampling_interval": 15, "horizon_mask": []}
# Polar night: the Sun neither sets nor rises, so the night window is noon to noon
TROMSO = {"name": "Tromsø", "lat": 69.65, "lon": 18.96, "timezone": "Europe/Oslo",
          "altitude_threshold": 10, "sampling_interval": 15, "horizon_mask": []}

_DRIVER = """
'use strict';
const fs = require('fs');
const vm = require('vm');
const [bodyFile, inFile, outFile, ...files] = process.argv.slice(2);
const context = {};
context.self = context;
context.globalThis = context;
vm.createContext(context);
for (const file of files) vm.runInContext(fs.readFileSync(file, 'utf8'), context, { filename: file });
context.input = JSON.parse(fs.readFileSync(inFile, 'utf8'));
vm.runInContext(fs.readFileSync(bodyFile, 'utf8'), context, { filename: 'case.js' });
fs.writeFileSync(outFile, JSON.stringify(context.result));
"""

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _run(tmp_path, body, payload=None):
    driver = tmp_path / "driver.js"
    driver.write_text(_DRIVER)
    case = tmp_path / "case.js"
    case.write_text(body)
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps(payload or {}))
    out = tmp_path / "out.json"
    run = subprocess.run([NODE, str(driver), str(case), str(inp), str(out)]
                         + [str(STATIC_JS / name) for name in FILES],
                         capture_output=True, text=True, check=False)
    assert run.returncode == 0, f"node failed ({run.returncode}):\n{run.stderr}"
    return json.loads(out.read_text())


def wind(start, hours, w200=10.0, w250=10.0, w300=10.0):
    """An /api/upper_wind answer, hourly from `start` (UTC); a level is a constant or a
    function of the hour's datetime."""
    times = [start + timedelta(hours=h) for h in range(hours)]
    at = lambda w, t: w(t) if callable(w) else w
    return {"times": [t.strftime("%Y-%m-%dT%H:%M:00Z") for t in times],
            "w200": [at(w200, t) for t in times], "w250": [at(w250, t) for t in times],
            "w300": [at(w300, t) for t in times], "unit": "m/s"}


H = 3600000
T0 = 1793491200000  # 2026-11-01T00:00:00Z, a whole hour


def test_thresholds_at_exactly_15_and_30(tmp_path):
    got = _run(tmp_path, "result = [14.9, 15, 15.01, 30, 30.01, 0].map(NovaSolarSeeing.level);")
    assert got == ["steady", "steady", "average", "average", "poor", "steady"]


def test_constants_are_named():
    """The thresholds the user decided, as named constants."""
    src = (STATIC_JS / "nova_solar_seeing.js").read_text()
    for line in ("const SEEING_STEADY_MAX_MS = 15;", "const SEEING_AVERAGE_MAX_MS = 30;",
                 "const SEEING_BEST_HOURS_HALF = 2;", "const SEEING_MIN_COVERAGE = 0.5;"):
        assert line in src


def test_hourly_is_the_highest_of_the_three_levels(tmp_path):
    """Per hour the maximum of 200/250/300 hPa, missing values skipped; all missing is null."""
    got = _run(tmp_path, "result = NovaSolarSeeing.hourly(input);", {
        "times": ["2026-11-01T00:00:00Z", "2026-11-01T01:00:00Z", "2026-11-01T02:00:00Z"],
        "w200": [10, None, None], "w250": [25, 7, None], "w300": [12, None, None]})
    assert got == {str(T0): 25, str(T0 + H): 7, str(T0 + 2 * H): None}


def test_median_rates_and_the_peak_goes_to_the_tooltip(tmp_path):
    """One gusty hour does not decide the night: median 10 (steady), peak 40. An even
    count takes the mean of the middle two."""
    got = _run(tmp_path, """
        const by = {}; [5, 10, 40, 12].forEach((v, i) => { by[input.t0 + i * input.h] = v; });
        const hours = n => Array.from({length: n}, (_, i) => input.t0 + i * input.h);
        result = [NovaSolarSeeing.rate(hours(3), by), NovaSolarSeeing.rate(hours(4), by)];
    """, {"t0": T0, "h": H})
    assert got[0] == {"level": "steady", "median": 10, "max": 40, "hours": 3, "covered": 3}
    assert got[1]["median"] == 11 and got[1]["level"] == "steady"


def test_missing_hours_are_skipped_and_coverage_below_half_gives_no_rating(tmp_path):
    got = _run(tmp_path, """
        const hours = Array.from({length: 4}, (_, i) => input.t0 + i * input.h);
        const by = vals => { const o = {}; vals.forEach((v, i) => { o[hours[i]] = v; }); return o; };
        result = [NovaSolarSeeing.rate(hours, by([20, null, 40, null])),
                  NovaSolarSeeing.rate(hours, by([20, null, null, null])),
                  NovaSolarSeeing.rate(hours, {}),
                  NovaSolarSeeing.rate([], by([1, 1, 1, 1]))];
    """, {"t0": T0, "h": H})
    assert got[0] == {"level": "average", "median": 30, "max": 40, "hours": 4, "covered": 2}
    assert got[1] == {"level": None, "reason": "nodata"}
    assert got[2] == {"level": None, "reason": "nodata"}
    assert got[3] == {"level": None, "reason": "nohours"}


def test_best_hours_around_transit_and_the_fallback(tmp_path):
    """Hours within ±2 h of transit and above the threshold; when none qualify (a transit
    in daylight, or none), every hour above the threshold."""
    got = _run(tmp_path, """
        const hours = NovaSolarSeeing.wholeHours(input.t0 - 1800000, input.t0 + 8 * input.h);
        const alts = [5, 25, 30, 40, 45, 40, 30, 15, 10];
        const best = transit => NovaSolarSeeing.bestHours(hours, alts, transit, 20).map(t => (t - input.t0) / input.h);
        result = {hours: hours.map(t => (t - input.t0) / input.h),
                  near: best(input.t0 + 4 * input.h), edge: best(input.t0 - 2 * input.h),
                  daylight: best(input.t0 - 12 * input.h), none: best(null)};
    """, {"t0": T0, "h": H})
    assert got["hours"] == list(range(9)), "whole hours inside the window, its end included"
    assert got["near"] == [2, 3, 4, 5, 6]
    assert got["edge"] == [1, 2, 3, 4, 5, 6], "nothing within 2 h above the threshold: fallback"
    assert got["daylight"] == [1, 2, 3, 4, 5, 6]
    assert got["none"] == [1, 2, 3, 4, 5, 6]


# The night of 2026-10-30 in London, as the Bodies table computes it
NIGHT = "2026-10-30"
ROWS = """
    const inputs = {location: input.loc};
    const night = NovaSolarRows._night;
    const site = night.site(inputs);
    const win = night.nightWindow(input.date, site, NovaAstro, NovaDashboardRows._wall, null);
"""


def test_tonight_uses_the_hours_around_the_tables_transit(tmp_path):
    """Calm (10 m/s) only within 2 h of Saturn's transit (mid-evening), stormy (50) elsewhere: the
    rating sees only the calm hours. The transit is the Bodies table row's."""
    got = _run(tmp_path, ROWS + """
        const nowMs = Date.UTC(2026, 9, 30, 21);
        const row = NovaSolarRows.rows({inputs: inputs, nowMs: nowMs, simDate: null}).rows.find(r => r.body === 'saturn');
        const t = row.transitUtc;
        const w = {times: [], w200: [], w250: [], w300: []};
        for (let h = Date.UTC(2026, 9, 29); h < Date.UTC(2026, 10, 6); h += 3600000) {
            w.times.push(new Date(h).toISOString());
            const calm = Math.abs(h - t) <= 2 * 3600000;
            w.w200.push(calm ? 10 : 50); w.w250.push(null); w.w300.push(calm ? 8 : 20);
        }
        const f = NovaSolarSeeing.forecast({inputs: inputs, body: 'saturn', date: input.date, wind: w, tonight: row});
        result = {f: f, inside: t >= win.start && t <= win.end, muted: row.muted};
    """, {"loc": LONDON, "date": NIGHT})
    assert got["inside"] and not got["muted"]
    tonight = got["f"]["tonight"]
    assert tonight["level"] == "steady" and tonight["median"] == 10 and tonight["max"] == 10
    assert 4 <= tonight["hours"] <= 5


def test_outlook_five_nights_the_last_two_less_reliable(tmp_path):
    got = _run(tmp_path, ROWS + """
        result = NovaSolarSeeing.forecast({inputs: inputs, body: 'jupiter', date: input.date, wind: input.wind});
    """, {"loc": LONDON, "date": NIGHT,
          "wind": wind(datetime(2026, 10, 29, tzinfo=timezone.utc), 8 * 24, w250=22)})
    outlook = got["outlook"]
    assert [n["date"] for n in outlook] == ["2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02", "2026-11-03"]
    assert [n["reliable"] for n in outlook] == [True, True, True, False, False]
    assert all(n["rating"]["level"] == "average" for n in outlook)
    assert got["tonight"] == outlook[0]["rating"]


def test_outlook_drops_nights_beyond_the_data(tmp_path):
    """Data until 2026-11-01 12:00 UTC: tonight and the next night have it, the night of
    11-01 starts after it ends; those after are left out, not shown empty."""
    got = _run(tmp_path, ROWS + """
        result = NovaSolarSeeing.forecast({inputs: inputs, body: 'jupiter', date: input.date, wind: input.wind});
    """, {"loc": LONDON, "date": NIGHT,
          "wind": wind(datetime(2026, 10, 29, tzinfo=timezone.utc), 3 * 24 + 12)})
    assert [n["date"] for n in got["outlook"]] == ["2026-10-30", "2026-10-31"]


def test_muted_body_has_no_rating(tmp_path):
    """Venus peaks at 4° that night, under the 20° threshold: no rating, but its outlook
    chips stay (shown without a rating word)."""
    got = _run(tmp_path, ROWS + """
        const row = NovaSolarRows.rows({inputs: inputs, nowMs: Date.UTC(2026, 9, 30, 21), simDate: null}).rows.find(r => r.body === 'venus');
        result = {muted: row.muted,
                  f: NovaSolarSeeing.forecast({inputs: inputs, body: 'venus', date: input.date, wind: input.wind, tonight: row})};
    """, {"loc": LONDON, "date": NIGHT, "wind": wind(datetime(2026, 10, 29, tzinfo=timezone.utc), 8 * 24)})
    assert got["muted"]
    assert got["f"]["tonight"] == {"level": None, "reason": "muted"}
    assert got["f"]["outlook"][0] == {"date": NIGHT, "rating": {"level": None, "reason": "muted"}, "reliable": True}


def test_polar_night_uses_the_noon_to_noon_window(tmp_path):
    """Tromsø in December: no sunset, so the window is local noon to noon, 24 whole hours
    plus the end; Jupiter gets its rating from the hours it is up."""
    got = _run(tmp_path, ROWS + """
        const hours = NovaSolarSeeing.wholeHours(win.start, win.end);
        result = {kind: win.kind, hours: hours.length,
                  f: NovaSolarSeeing.forecast({inputs: inputs, body: 'jupiter', date: input.date, wind: input.wind})};
    """, {"loc": TROMSO, "date": "2026-12-20", "wind": wind(datetime(2026, 12, 19, tzinfo=timezone.utc), 7 * 24, w300=35)})
    assert got["kind"] == "noon" and got["hours"] == 25
    assert got["f"]["tonight"]["level"] == "poor" and got["f"]["tonight"]["median"] == 35


def test_no_wind_is_no_data_everywhere(tmp_path):
    """The endpoint's empty answer: no rating tonight and an empty outlook."""
    got = _run(tmp_path, ROWS + """
        result = NovaSolarSeeing.forecast({inputs: inputs, body: 'jupiter', date: input.date,
                                            wind: {times: [], w200: [], w250: [], w300: []}});
    """, {"loc": LONDON, "date": NIGHT})
    assert got == {"tonight": {"level": None, "reason": "nodata"}, "outlook": []}
