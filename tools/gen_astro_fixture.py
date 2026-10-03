#!/usr/bin/env python3
"""
Generate the reference fixture for the JavaScript astro engine (static/js/nova_astro.js).

Runs Nova's own Python functions (astropy + ephem) over a fixed set of objects,
locations and dates and writes tests/fixtures/astro_reference.json.
tools/compare_astro_engine.js then checks the JS engine against that file.

Run from the repo root:   python tools/gen_astro_fixture.py
Re-run only when modules/astro_calculations.py changes its results on purpose.
"""
import json
import os
import sys
import warnings

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings("ignore")

import astropy.units as u  # noqa: E402
from astropy.coordinates import AltAz, EarthLocation, SkyCoord, get_body  # noqa: E402
from astropy.time import Time  # noqa: E402

from modules.astro_calculations import (  # noqa: E402
    calculate_moon_phase_cached,
    calculate_observable_duration_vectorized,
    calculate_sun_events,
    get_common_time_arrays,
    get_utc_time_for_local_11pm_on,
)

OUT = os.path.join("tests", "fixtures", "astro_reference.json")

# name, RA (hours), Dec (degrees), J2000
OBJECTS = [
    ("M31", 0.7123, 41.269), ("NGC 104", 0.4014, -72.081), ("NGC 253", 0.7925, -25.288),
    ("SMC", 0.8767, -72.800), ("M33", 1.5642, 30.660), ("M45", 3.7900, 24.117),
    ("California", 4.0500, 36.417), ("LMC", 5.3929, -69.756), ("M1", 5.5756, 22.015),
    ("M42", 5.5881, -5.391), ("Rosette", 6.5300, 4.950), ("NGC 2403", 7.6142, 65.603),
    ("M81", 9.9258, 69.065), ("Carina", 10.7500, -59.867), ("M104", 12.6664, -11.623),
    ("Omega Cen", 13.4464, -47.479), ("M51", 13.4981, 47.195), ("M101", 14.0536, 54.349),
    ("M13", 16.6950, 36.460), ("M8", 18.0600, -24.383), ("M57", 18.8931, 33.029),
    ("NGC 6744", 19.1628, -63.857), ("M27", 19.9936, 22.721), ("NGC 7000", 20.9800, 44.333),
    ("Helix", 22.4936, -20.837), ("NGC 7293b", 23.9900, -0.500), ("Edge RA0", 0.0010, 0.000),
    ("Near N pole", 2.5300, 89.264), ("Near S pole", 21.1500, -88.956), ("Mid south", 15.0000, -33.000),
]

MASK_HOME = [[60.0, 28.0], [90.0, 35.0], [120.0, 31.0], [150.0, 22.0]]
MASK_WRAP = [[300.0, 40.0], [330.0, 45.0], [355.0, 30.0]]

# name, lat, lon, tz, altitude threshold, sampling interval, horizon mask
LOCATIONS = [
    ("Bad Fischau", 47.8300, 16.1700, "Europe/Vienna", 20, 15, MASK_HOME),
    ("Singapore", 1.3521, 103.8198, "Asia/Singapore", 30, 15, None),
    ("Perth", -31.9523, 115.8613, "Australia/Perth", 20, 10, None),
    ("Los Angeles", 34.0522, -118.2437, "America/Los_Angeles", 25, 15, MASK_WRAP),
    ("Santiago", -33.4489, -70.6693, "America/Santiago", 20, 15, None),
    ("Tromso", 69.6492, 18.9553, "Europe/Oslo", 20, 15, None),
    ("Reykjavik", 64.1466, -21.9426, "Atlantic/Reykjavik", 15, 15, None),
]

DATES = [
    "2026-10-15", "2026-10-25", "2026-11-15", "2026-12-15", "2026-12-21", "2027-01-15",
    "2027-02-15", "2027-03-15", "2027-03-28", "2027-04-15", "2027-05-15", "2027-06-15",
    "2027-06-21", "2027-07-15", "2027-08-15", "2027-09-15",
]

# Fractions of the 24 h noon-to-noon grid at which alt/az are stored
SAMPLE_FRACTIONS = [0.0, 0.2, 0.35, 0.45, 0.5, 0.55, 0.7, 0.9]


def r(x, n=4):
    return round(float(x), n)


def hm(dt):
    return dt.strftime("%Y-%m-%dT%H:%M") if dt is not None else None


def main():
    cases = []
    for name, lat, lon, tz, thr, interval, mask in LOCATIONS:
        earth = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
        for d in DATES:
            sun = calculate_sun_events(d, tz, lat, lon)
            phase = calculate_moon_phase_cached(d, lat, lon)

            times_local, times_utc = get_common_time_arrays(tz, d, interval)
            idx = [int(f * len(times_local)) for f in SAMPLE_FRACTIONS]
            sample_utc = [times_utc[i].isot[:19] for i in idx]

            # Moon exactly as nova/helpers.py does it, at 23:00 local
            t_ref_str = get_utc_time_for_local_11pm_on(d, tz)
            t_ref = Time(t_ref_str, format="isot", scale="utc")
            moon = get_body("moon", t_ref, earth)
            frame_ref = AltAz(obstime=t_ref, location=earth)
            moon_in_frame = moon.transform_to(frame_ref)

            objs = []
            for oname, ra, dec in OBJECTS:
                sc = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
                aa = sc.transform_to(AltAz(obstime=times_utc[idx], location=earth))
                dur, max_alt, o_from, o_to = calculate_observable_duration_vectorized(
                    ra, dec, lat, lon, d, tz, thr, interval, mask)
                sep = sc.transform_to(frame_ref).separation(moon_in_frame).deg
                objs.append({
                    "alt": [r(v) for v in aa.alt.deg],
                    "az": [r(v) for v in aa.az.deg],
                    "dur": int(dur.total_seconds() / 60),
                    "max_alt": r(max_alt),
                    "from": hm(o_from),
                    "to": hm(o_to),
                    "moon_sep": r(sep),
                })

            cases.append({
                "location": name, "lat": lat, "lon": lon, "tz": tz,
                "threshold": thr, "interval": interval, "mask": mask,
                "date": d, "sun": sun, "moon_phase": phase,
                "grid_len": len(times_local),
                "grid_first_utc": times_utc[0].isot[:19],
                "sample_utc": sample_utc,
                "ref_utc": t_ref_str,
                "moon_alt": r(moon_in_frame.alt.deg), "moon_az": r(moon_in_frame.az.deg),
                "objects": objs,
            })
        print(f"{name}: done", file=sys.stderr)

    payload = {
        "objects": [{"name": n, "ra": ra, "dec": dec} for n, ra, dec in OBJECTS],
        "cases": cases,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"wrote {OUT}: {len(cases)} cases x {len(OBJECTS)} objects, "
          f"{os.path.getsize(OUT) // 1024} KB", file=sys.stderr)


if __name__ == "__main__":
    main()
