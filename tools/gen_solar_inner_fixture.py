#!/usr/bin/env python3
"""
Generate the reference fixture for the Venus and Mercury cards' rules
(static/js/nova_solar_inner.js): tests/fixtures/solar_inner.json.

Two independent sources, both offline, neither of them Nova's own JavaScript:

  1. ephem 4.2 (the same library the shared planet engine was built against) gives the
     five quantities the cards read at the observing instant, for both bodies:

         mag              apparent visual magnitude
         diameterArcsec   apparent angular diameter, arcseconds
         illuminated      illuminated fraction, 0..1
         phaseAngleDeg    the Sun-planet-Earth angle, degrees
         elongationDeg    signed solar elongation, degrees, + east of the Sun

     ephem reports the illuminated *percentage* and not the phase angle; the phase
     angle here is recovered from it as acos(2k - 1), which is a different route to
     the number than the engine's own law of cosines and so a real check.

     The dates are the 157 weekly instants 2026-01-01 .. 2028-12-28 at 00:00 UT, the
     same instants the Horizons extract below carries, so one date list serves both.

  2. A JPL Horizons extract, checked in verbatim under tests/fixtures/horizons/,
     gives the IAU pole's position angle NP.ang (quantity 17, counter-clockwise from
     the true-of-date north) and its angular distance NP.dist from the sub-observer
     point in arcseconds, weekly over the same three years.

     The two files were fetched from
         https://ssd.jpl.nasa.gov/api/horizons.api
     with, for Mercury (199) and Venus (299), geocentric:

         COMMAND='199'  (or '299')
         EPHEM_TYPE='OBSERVER'  CENTER='500@399'
         START_TIME='2026-01-01'  STOP_TIME='2028-12-31'  STEP_SIZE='7 d'
         QUANTITIES='17'  ANG_FORMAT='DEG'  EXTRA_PREC='YES'  CSV_FORMAT='YES'

     Horizons' NP.ang is the whole point of checking in a raw extract: the pole
     elements in the module are IAU/WGCCRE values, and this is an independent
     ephemeris turning them into the angle the card actually draws.

     The raw files are kept beside the fixture and parsed here rather than
     transcribed, so the numbers in solar_inner.json can be traced to a file that
     carries its own header (target, epoch, step, column definitions).

Run from the repo root:   ./venv/bin/python tools/gen_solar_inner_fixture.py
Re-run only when the engine or the module changes its results on purpose. The
Horizons sections are re-parsed, never re-fetched: nothing here touches the network.
"""
import json
import math
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ephem  # noqa: E402

FIXTURES = os.path.join("tests", "fixtures")
HORIZONS = os.path.join(FIXTURES, "horizons")
OUT = os.path.join(FIXTURES, "solar_inner.json")

# The weekly instants, the same as the Horizons step: 157 dates, 2026-01-01 + 7n days.
START = "2026/01/01 00:00:00"
COUNT = 157
STEP_DAYS = 7

# name, ephem class, the Horizons extract, the Horizons target id and pole frame.
BODIES = [
    ("mercury", ephem.Mercury, "mercury_np_ang_2026_2028.txt", "199", "IAU_MERCURY"),
    ("venus", ephem.Venus, "venus_np_ang_2026_2028.txt", "299", "IAU_VENUS"),
]


def dates():
    """The 157 weekly instants as (YYYY-MM-DD, ephem.Date)."""
    out = []
    for i in range(COUNT):
        d = ephem.Date(START) + i * STEP_DAYS
        out.append((ephem.Date(d).datetime().strftime("%Y-%m-%d"), d))
    return out


def ephem_rows():
    """The five quantities above, per body, at every date."""
    ds = dates()
    rows = {}
    for name, cls, _file, _id, _frame in BODIES:
        rows[name] = []
        for iso, d in ds:
            b = cls()
            b.compute(d)
            k = float(b.phase) / 100.0
            rows[name].append({
                "date": iso,
                # ephem's own reporting units, which are not uniform: mag as it comes,
                # size in arcseconds, phase as a percentage, and elong -- an Angle with
                # no stated type -- in radians, though its own repr prints degrees.
                # Checked against Horizons ang-sep: -1.45269 deg against 1.45260 deg on
                # 2026-01-01, west of the Sun and so negative.
                "mag": round(float(b.mag), 4),
                "diameterArcsec": round(float(b.size), 6),
                "illuminated": round(k, 8),
                # From the illuminated fraction, not from the geometry: the engine
                # reaches the same angle by the law of cosines.
                "phaseAngleDeg": round(math.degrees(
                    math.acos(max(-1.0, min(1.0, 2.0 * k - 1.0)))), 6),
                "elongationDeg": round(math.degrees(float(b.elong)), 6),
            })
    return rows


# Horizons' CSV block sits between $$SOE and $$EOE; NP.ang is degrees with ANG_FORMAT=DEG,
# NP.dist is arcseconds (the extract's own trailer says "Units: DEGREES and ARCSECONDS").
_ROW = re.compile(r"^\s*(\d{4})-([A-Za-z]{3})-(\d{2})\s+(\d{2}):(\d{2})\s*,\s*(?:,\s*)*"
                  r"([-\d.]+)\s*,\s*([-\d.]+)\s*,?\s*$")
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}


def horizons_rows():
    """Parse each raw extract into date, NP.ang (degrees) and NP.dist (arcseconds)."""
    out = {}
    for name, _cls, filename, target, frame in BODIES:
        path = os.path.join(HORIZONS, filename)
        text = open(path).read()
        # The column header line names the quantities; refuse an extract that is not the
        # one this fixture is about rather than silently reading the wrong columns.
        head = text.split("$$SOE")[0]
        columns = [ln for ln in head.splitlines() if "Date__(UT)" in ln]
        assert columns and "NP.ang" in columns[-1], f"{filename}: no NP.ang column"
        assert f"Target body name: " in head and f"({target})" in head, \
            f"{filename}: not target {target}"
        assert frame in head, f"{filename}: not the {frame} pole"

        block = text.split("$$SOE")[1].split("$$EOE")[0]
        rows = []
        for line in block.splitlines():
            m = _ROW.match(line)
            if not m:
                continue
            y, mon, day, _hh, _mm, ang, dist = m.groups()
            rows.append({
                "date": f"{y}-{_MONTHS[mon]:02d}-{int(day):02d}",
                "npAngDeg": round(float(ang), 4),
                "npDistArcsec": round(float(dist), 3),
            })
        assert len(rows) == COUNT, f"{filename}: {len(rows)} rows, expected {COUNT}"
        out[name] = {
            "source": "JPL Horizons, quantity 17 (NP.ang, NP.dist), geocentric, "
                      "STEP_SIZE='7 d', ANG_FORMAT='DEG', CSV_FORMAT='YES'",
            "file": "tests/fixtures/horizons/" + filename,
            "target": target,
            "poleFrame": frame,
            "stepDays": STEP_DAYS,
            "rows": rows,
        }
    return out


def main():
    iso_dates = [iso for iso, _d in dates()]
    ephem_data = ephem_rows()
    horizons_data = horizons_rows()

    # Both sections must be about the same instants, or the test would be comparing
    # different dates and passing by accident.
    for name in ephem_data:
        assert [r["date"] for r in ephem_data[name]] == iso_dates, name
        assert [r["date"] for r in horizons_data[name]["rows"]] == iso_dates, name

    fixture = {
        "note": "Reference values for the Venus and Mercury cards "
                "(static/js/nova_solar_inner.js). Generated by "
                "tools/gen_solar_inner_fixture.py; see its docstring for the sources, "
                "the Horizons query and the units.",
        "ephem": {
            "version": ephem.__version__,
            "source": "ephem, geocentric at 00:00 UT; the illuminated fraction is "
                      "ephem's phase percentage / 100 and phaseAngleDeg is "
                      "acos(2k - 1) of it",
            "start": iso_dates[0],
            "stepDays": STEP_DAYS,
            "bodies": ephem_data,
        },
        "horizons": horizons_data,
    }
    with open(OUT, "w") as fh:
        json.dump(fixture, fh, indent=1, sort_keys=False)
        fh.write("\n")
    print(f"wrote {OUT}: {COUNT} dates x {len(BODIES)} bodies, "
          f"ephem {ephem.__version__}, {len(horizons_data)} Horizons extracts")


if __name__ == "__main__":
    main()
