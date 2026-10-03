/*
 * nova_astro.js — Nova DSO Tracker astro engine (JavaScript)
 *
 * One dependency-free file, shared by the mobile web pages (/m) and the iOS app.
 * It reproduces the results of modules/astro_calculations.py (astropy + ephem)
 * closely enough for planning: positions to a few arcseconds, twilight to the minute.
 *
 * Validated against the Python reference with tools/compare_astro_engine.js
 * (fixture: tests/fixtures/astro_reference.json, built by tools/gen_astro_fixture.py).
 * Any change to this file must keep that comparison green.
 *
 * Conventions
 *   - Times are UTC milliseconds since the Unix epoch (Date.now()).
 *   - RA in hours (J2000 / ICRS), Dec, latitude, longitude, altitude, azimuth in degrees.
 *   - Azimuth is measured from north through east. No atmospheric refraction, like astropy
 *     with pressure 0.
 *   - Dates are 'YYYY-MM-DD' strings naming the observing night (noon to noon, local time).
 *   - Time zones are IANA names; conversion uses Intl, which every target browser provides.
 */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.NovaAstro = factory();
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const PI = Math.PI, TAU = 2 * Math.PI;
    const DEG = PI / 180, HOUR = PI / 12, ARCSEC = DEG / 3600;
    const DAY_MS = 86400000;
    const sin = Math.sin, cos = Math.cos;

    // ephem applies its refraction model to the -0.833 degree horizon that
    // calculate_sun_events() asks for, so Nova's sunrise and sunset are the moments
    // the Sun's centre is at this true altitude. (At -18 degrees ephem applies none.)
    const SUN_RISE_SET_ALT = -1.6195566;
    const ASTRO_TWILIGHT_ALT = -18;

    const mod = (a, n) => ((a % n) + n) % n;
    const plusMinusPi = a => mod(a - PI, TAU) - PI;
    const clamp1 = x => (x > 1 ? 1 : x < -1 ? -1 : x);

    // ------------------------------------------------------------------ time zones

    const fmtCache = {};
    function tzFormatter(tz) {
        let f = fmtCache[tz];
        if (!f) {
            f = fmtCache[tz] = new Intl.DateTimeFormat('en-US', {
                timeZone: tz, hourCycle: 'h23',
                year: 'numeric', month: 'numeric', day: 'numeric',
                hour: 'numeric', minute: 'numeric', second: 'numeric',
            });
        }
        return f;
    }

    /** Wall-clock fields of a UTC instant in a time zone. */
    function localParts(utcMs, tz) {
        const p = {};
        for (const part of tzFormatter(tz).formatToParts(new Date(utcMs))) {
            if (part.type !== 'literal') p[part.type] = parseInt(part.value, 10);
        }
        return { year: p.year, month: p.month, day: p.day, hour: p.hour % 24, minute: p.minute, second: p.second };
    }

    function tzOffsetMs(utcMs, tz) {
        const p = localParts(utcMs, tz);
        const asUtc = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
        return asUtc - Math.floor(utcMs / 1000) * 1000;
    }

    /** UTC instant of a local wall-clock time. Day overflow is allowed (day 32 rolls over). */
    function localToUtc(year, month, day, hour, minute, tz) {
        const wall = Date.UTC(year, month - 1, day, hour, minute, 0);
        let utc = wall - tzOffsetMs(wall, tz);
        const off = tzOffsetMs(utc, tz);
        if (wall - off !== utc) {
            // Near a DST change. Prefer standard time, as pytz does by default.
            const alt = wall - off;
            utc = tzOffsetMs(alt, tz) === off ? alt : Math.max(utc, alt);
        }
        return utc;
    }

    function parseDate(dateStr) {
        const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateStr);
        if (!m) throw new Error('Invalid date: ' + dateStr);
        return { year: +m[1], month: +m[2], day: +m[3] };
    }

    const pad2 = n => (n < 10 ? '0' : '') + n;

    /** 'HH:MM' local time, truncated to the minute like Python's strftime. */
    function formatHM(utcMs, tz) {
        const p = localParts(utcMs, tz);
        return pad2(p.hour) + ':' + pad2(p.minute);
    }

    /** The observing night a moment belongs to: before local noon it is still yesterday's. */
    function observingDate(utcMs, tz) {
        const p = localParts(utcMs - 12 * 3600000, tz);
        return p.year + '-' + pad2(p.month) + '-' + pad2(p.day);
    }

    /** Sample times from local noon for 24 hours (get_common_time_arrays). */
    function timeGrid(dateStr, tz, intervalMinutes) {
        const step = intervalMinutes || 15;
        const d = parseDate(dateStr);
        const start = localToUtc(d.year, d.month, d.day, 12, 0, tz);
        const n = Math.floor(24 * 60 / step);
        const out = new Float64Array(n);
        for (let i = 0; i < n; i++) out[i] = start + i * step * 60000;
        return out;
    }

    // ------------------------------------------------------------------ Earth orientation

    const jdOf = utcMs => utcMs / DAY_MS + 2440587.5;
    const centuries = utcMs => (jdOf(utcMs) - 2451545.0) / 36525;

    /** Nutation in longitude and obliquity, and the mean obliquity (radians). */
    function nutation(T) {
        const om = (125.04452 - 1934.136261 * T) * DEG;
        const L = (280.4665 + 36000.7698 * T) * DEG;
        const Lm = (218.3165 + 481267.8813 * T) * DEG;
        const dpsi = (-17.20 * sin(om) - 1.32 * sin(2 * L) - 0.23 * sin(2 * Lm) + 0.21 * sin(2 * om)) * ARCSEC;
        const deps = (9.20 * cos(om) + 0.57 * cos(2 * L) + 0.10 * cos(2 * Lm) - 0.09 * cos(2 * om)) * ARCSEC;
        const eps0 = (23.4392911 - 0.0130042 * T - 1.64e-7 * T * T + 5.04e-7 * T * T * T) * DEG;
        return { dpsi, deps, eps0, eps: eps0 + deps };
    }

    function gmst(utcMs) {
        const d = jdOf(utcMs) - 2451545.0, T = d / 36525;
        return mod(280.46061837 + 360.98564736629 * d + 0.000387933 * T * T - T * T * T / 38710000, 360) * DEG;
    }

    /** Apparent sidereal time at Greenwich (radians). UTC is used as UT1. */
    function gast(utcMs, nut) {
        const n = nut || nutation(centuries(utcMs));
        return gmst(utcMs) + n.dpsi * cos(n.eps);
    }

    // ------------------------------------------------------------------ Sun

    /** Apparent geocentric Sun: RA, Dec (radians, true equinox of date), distance (AU), longitude. */
    function sunPosition(utcMs) {
        const T = centuries(utcMs);
        const L0 = 280.46646 + 36000.76983 * T + 0.0003032 * T * T;
        const M = (357.52911 + 35999.05029 * T - 0.0001537 * T * T) * DEG;
        const e = 0.016708634 - 0.000042037 * T - 1.267e-7 * T * T;
        const C = (1.914602 - 0.004817 * T - 0.000014 * T * T) * sin(M)
            + (0.019993 - 0.000101 * T) * sin(2 * M) + 0.000289 * sin(3 * M);
        const trueLon = L0 + C;
        const nu = M + C * DEG;
        const R = 1.000001018 * (1 - e * e) / (1 + e * cos(nu));
        const n = nutation(T);
        const lam = trueLon * DEG - 20.4898 * ARCSEC / R + n.dpsi;
        const ra = Math.atan2(cos(n.eps) * sin(lam), cos(lam));
        const dec = Math.asin(clamp1(sin(n.eps) * sin(lam)));
        return { ra: mod(ra, TAU), dec, dist: R, lon: mod(lam, TAU), trueLon: mod(trueLon * DEG, TAU), nut: n };
    }

    // ------------------------------------------------------------------ fixed objects

    /**
     * Everything that depends on the date but not on the object: the rotation from
     * J2000 to the true equator and equinox of date, and the annual aberration vector.
     * It changes by well under an arcsecond in a night, so build it once per night.
     */
    function frameFor(utcMs) {
        const T = centuries(utcMs);
        const n = nutation(T);

        // Precession J2000 -> mean of date (IAU 1976)
        const zeta = (2306.2181 * T + 0.30188 * T * T + 0.017998 * T * T * T) * ARCSEC;
        const z = (2306.2181 * T + 1.09468 * T * T + 0.018203 * T * T * T) * ARCSEC;
        const th = (2004.3109 * T - 0.42665 * T * T - 0.041833 * T * T * T) * ARCSEC;
        const cz = cos(zeta), sz = sin(zeta), cZ = cos(z), sZ = sin(z), ct = cos(th), st = sin(th);
        const P = [
            cZ * ct * cz - sZ * sz, -cZ * ct * sz - sZ * cz, -cZ * st,
            sZ * ct * cz + cZ * sz, -sZ * ct * sz + cZ * cz, -sZ * st,
            st * cz, -st * sz, ct,
        ];

        // Nutation mean -> true of date
        const ce = cos(n.eps0), se = sin(n.eps0), ct2 = cos(n.eps), st2 = sin(n.eps);
        const cp = cos(n.dpsi), sp = sin(n.dpsi);
        const N = [
            cp, -sp * ce, -sp * se,
            sp * ct2, cp * ct2 * ce + st2 * se, cp * ct2 * se - st2 * ce,
            sp * st2, cp * st2 * ce - ct2 * se, cp * st2 * se + ct2 * ce,
        ];

        const M = new Float64Array(9);
        for (let i = 0; i < 3; i++) {
            for (let j = 0; j < 3; j++) {
                M[i * 3 + j] = N[i * 3] * P[j] + N[i * 3 + 1] * P[3 + j] + N[i * 3 + 2] * P[6 + j];
            }
        }

        // Annual aberration: Earth's velocity over c, in the J2000 equatorial frame
        const s = sunPosition(utcMs);
        const k = 20.49552 * ARCSEC;
        const e = 0.016708634 - 0.000042037 * T;
        const peri = (102.93735 + 1.71946 * T) * DEG;
        const lam = s.trueLon, e0 = 23.4392911 * DEG;
        const vx = k * (sin(lam) - e * sin(peri));
        const vyEcl = k * (-cos(lam) + e * cos(peri));
        return { M, v: [vx, vyEcl * cos(e0), vyEcl * sin(e0)], nut: n };
    }

    /** Apparent place of a J2000 position: { ra, dec } in radians, true equator of date. */
    function apparent(raHours, decDeg, frame) {
        const a = raHours * HOUR, d = decDeg * DEG, cd = cos(d);
        let x = cd * cos(a) + frame.v[0], y = cd * sin(a) + frame.v[1], zc = sin(d) + frame.v[2];
        const M = frame.M;
        const X = M[0] * x + M[1] * y + M[2] * zc;
        const Y = M[3] * x + M[4] * y + M[5] * zc;
        const Z = M[6] * x + M[7] * y + M[8] * zc;
        return { ra: Math.atan2(Y, X), dec: Math.atan2(Z, Math.sqrt(X * X + Y * Y)) };
    }

    function horizontal(ra, dec, lst, latRad) {
        const H = lst - ra;
        const sd = sin(dec), cd = cos(dec), sl = sin(latRad), cl = cos(latRad), cH = cos(H);
        const alt = Math.asin(clamp1(sl * sd + cl * cd * cH));
        const az = Math.atan2(-cd * sin(H), sd * cl - cd * cH * sl);
        return { alt: alt / DEG, az: mod(az, TAU) / DEG };
    }

    /** Altitude and azimuth of a fixed object at one moment. */
    function altAz(raHours, decDeg, lat, lon, utcMs, frame) {
        const f = frame || frameFor(utcMs);
        const p = apparent(raHours, decDeg, f);
        return horizontal(p.ra, p.dec, gast(utcMs, f.nut) + lon * DEG, lat * DEG);
    }

    /** Altitude and azimuth curves over an array of times: { alt: Float64Array, az: Float64Array }. */
    function altAzCurve(raHours, decDeg, lat, lon, times, frame) {
        const n = times.length;
        const alt = new Float64Array(n), az = new Float64Array(n);
        if (!n) return { alt, az };
        const f = frame || frameFor(times[n >> 1]);
        const p = apparent(raHours, decDeg, f);
        const sd = sin(p.dec), cd = cos(p.dec), latRad = lat * DEG, sl = sin(latRad), cl = cos(latRad);
        const eqeq = f.nut.dpsi * cos(f.nut.eps), lonRad = lon * DEG;
        for (let i = 0; i < n; i++) {
            const H = gmst(times[i]) + eqeq + lonRad - p.ra;
            const cH = cos(H);
            alt[i] = Math.asin(clamp1(sl * sd + cl * cd * cH)) / DEG;
            az[i] = mod(Math.atan2(-cd * sin(H), sd * cl - cd * cH * sl), TAU) / DEG;
        }
        return { alt, az };
    }

    // ------------------------------------------------------------------ Sun events

    // ephem.Observer._find_rise_or_set, step for step, so the minute-level results agree.
    function findRiseSet(startMs, latRad, lonRad, horizonDeg, rising) {
        const precision = 0.1 / 86400;
        const sinAlt = sin(horizonDeg * DEG);
        let t = startMs, prevHa = null, absTarget = 0;
        for (let i = 0; i < 7; i++) {
            const s = sunPosition(t);
            const ha = mod(gast(t, s.nut) + lonRad - s.ra, TAU);
            const arg = (sinAlt - sin(latRad) * sin(s.dec)) / (cos(latRad) * cos(s.dec));
            absTarget = arg < -1 ? PI + 1e-15 : arg > 1 ? -1e-15 : Math.acos(arg);
            let diff = (rising ? -absTarget : absTarget) - ha, bump;
            if (prevHa === null) {
                diff = mod(diff, TAU);
                bump = diff / TAU;
                if (Math.abs(bump) < precision) bump += 1;
            } else {
                bump = plusMinusPi(diff) / TAU;
            }
            if (Math.abs(bump) < precision) break;
            t += bump * DAY_MS;
            prevHa = ha;
        }
        if (absTarget > PI || absTarget < 0) return null; // never sets / never rises
        return t;
    }

    function findSunTransit(startMs, lonRad) {
        let t = startMs;
        for (let i = 0; i < 7; i++) {
            const s = sunPosition(t);
            const ha = mod(gast(t, s.nut) + lonRad - s.ra, TAU);
            const bump = (i === 0 ? mod(-ha, TAU) : plusMinusPi(-ha)) / TAU;
            t += bump * DAY_MS;
            if (Math.abs(bump) < 0.1 / 86400) break;
        }
        return t;
    }

    /**
     * Same keys and 'HH:MM' / 'N/A' values as calculate_sun_events().
     * The *_utc fields are extra: the same events as UTC ms (or null).
     */
    function sunEvents(dateStr, tz, lat, lon) {
        const d = parseDate(dateStr);
        const latRad = lat * DEG, lonRad = lon * DEG;
        const midnight = localToUtc(d.year, d.month, d.day, 0, 0, tz);
        const noon = localToUtc(d.year, d.month, d.day, 12, 0, tz);

        const sunrise = findRiseSet(midnight, latRad, lonRad, SUN_RISE_SET_ALT, true);
        const transit = findSunTransit(midnight, lonRad);
        const sunset = findRiseSet(noon, latRad, lonRad, SUN_RISE_SET_ALT, false);
        const dusk = findRiseSet(noon, latRad, lonRad, ASTRO_TWILIGHT_ALT, false);
        const dawn = findRiseSet(dusk !== null ? dusk : midnight, latRad, lonRad, ASTRO_TWILIGHT_ALT, true);

        const hm = t => (t === null ? 'N/A' : formatHM(t, tz));
        return {
            astronomical_dawn: hm(dawn), sunrise: hm(sunrise), transit: hm(transit),
            sunset: hm(sunset), astronomical_dusk: hm(dusk),
            astronomical_dawn_utc: dawn, sunrise_utc: sunrise, transit_utc: transit,
            sunset_utc: sunset, astronomical_dusk_utc: dusk,
        };
    }

    // ------------------------------------------------------------------ Moon

    // Periodic terms for the Moon's longitude and distance: D, M, M', F, sigma-l, sigma-r
    const MOON_LR = [
        0, 0, 1, 0, 6288774, -20905355, 2, 0, -1, 0, 1274027, -3699111, 2, 0, 0, 0, 658314, -2955968,
        0, 0, 2, 0, 213618, -569925, 0, 1, 0, 0, -185116, 48888, 0, 0, 0, 2, -114332, -3149,
        2, 0, -2, 0, 58793, 246158, 2, -1, -1, 0, 57066, -152138, 2, 0, 1, 0, 53322, -170733,
        2, -1, 0, 0, 45758, -204586, 0, 1, -1, 0, -40923, -129620, 1, 0, 0, 0, -34720, 108743,
        0, 1, 1, 0, -30383, 104755, 2, 0, 0, -2, 15327, 10321, 0, 0, 1, 2, -12528, 0,
        0, 0, 1, -2, 10980, 79661, 4, 0, -1, 0, 10675, -34782, 0, 0, 3, 0, 10034, -23210,
        4, 0, -2, 0, 8548, -21636, 2, 1, -1, 0, -7888, 24208, 2, 1, 0, 0, -6766, 30824,
        1, 0, -1, 0, -5163, -8379, 1, 1, 0, 0, 4987, -16675, 2, -1, 1, 0, 4036, -12831,
        2, 0, 2, 0, 3994, -10445, 4, 0, 0, 0, 3861, -11650, 2, 0, -3, 0, 3665, 14403,
        0, 1, -2, 0, -2689, -7003, 2, 0, -1, 2, -2602, 0, 2, -1, -2, 0, 2390, 10056,
        1, 0, 1, 0, -2348, 6322, 2, -2, 0, 0, 2236, -9884, 0, 1, 2, 0, -2120, 5751,
        0, 2, 0, 0, -2069, 0, 2, -2, -1, 0, 2048, -4950, 2, 0, 1, -2, -1773, 4130,
        2, 0, 0, 2, -1595, 0, 4, -1, -1, 0, 1215, -3958, 0, 0, 2, 2, -1110, 0,
        3, 0, -1, 0, -892, 3258, 2, 1, 1, 0, -810, 2616, 4, -1, -2, 0, 759, -1897,
        0, 2, -1, 0, -713, -2117, 2, 2, -1, 0, -700, 2354, 2, 1, -2, 0, 691, 0,
        2, -1, 0, -2, 596, 0, 4, 0, 1, 0, 549, -1423, 0, 0, 4, 0, 537, -1117,
        4, -1, 0, 0, 520, -1571, 1, 0, -2, 0, -487, -1739, 2, 1, 0, -2, -399, 0,
        0, 0, 2, -2, -381, -4421, 1, 1, 1, 0, 351, 0, 3, 0, -2, 0, -340, 0,
        4, 0, -3, 0, 330, 0, 2, -1, 2, 0, 327, 0, 0, 2, 1, 0, -323, 1165,
        1, 1, -1, 0, 299, 0, 2, 0, 3, 0, 294, 0, 2, 0, -1, -2, 0, 8752,
    ];
    // Periodic terms for the Moon's latitude: D, M, M', F, sigma-b
    const MOON_B = [
        0, 0, 0, 1, 5128122, 0, 0, 1, 1, 280602, 0, 0, 1, -1, 277693, 2, 0, 0, -1, 173237,
        2, 0, -1, 1, 55413, 2, 0, -1, -1, 46271, 2, 0, 0, 1, 32573, 0, 0, 2, 1, 17198,
        2, 0, 1, -1, 9266, 0, 0, 2, -1, 8822, 2, -1, 0, -1, 8216, 2, 0, -2, -1, 4324,
        2, 0, 1, 1, 4200, 2, 1, 0, -1, -3359, 2, -1, -1, 1, 2463, 2, -1, 0, 1, 2211,
        2, -1, -1, -1, 2065, 0, 1, -1, -1, -1870, 4, 0, -1, -1, 1828, 0, 1, 0, 1, -1794,
        0, 0, 0, 3, -1749, 0, 1, -1, 1, -1565, 1, 0, 0, 1, -1491, 0, 1, 1, 1, -1475,
        0, 1, 1, -1, -1410, 0, 1, 0, -1, -1344, 1, 0, 0, -1, -1335, 0, 0, 3, 1, 1107,
        4, 0, 0, -1, 1021, 4, 0, -1, 1, 833, 0, 0, 1, -3, 777, 4, 0, -2, 1, 671,
        2, 0, 0, -3, 607, 2, 0, 2, -1, 596, 2, -1, 1, -1, 491, 2, 0, -2, 1, -451,
        0, 0, 3, -1, 439, 2, 0, 2, 1, 422, 2, 0, -3, -1, 421, 2, 1, -1, 1, -366,
        2, 1, 0, 1, -351, 4, 0, 0, 1, 331, 2, -1, 1, 1, 315, 2, -2, 0, -1, 302,
        0, 0, 1, 3, -283, 2, 1, 1, -1, -229, 1, 1, 0, -1, 223, 1, 1, 0, 1, 223,
        0, 1, -2, -1, -220, 2, 1, -1, -1, -220, 1, 0, 1, 1, -185, 2, -1, -2, -1, 181,
        0, 1, 2, 1, -177, 4, 0, -2, -1, 176, 4, -1, -1, -1, 166, 1, 0, 1, -1, -164,
        4, 0, 1, -1, 132, 1, 0, -1, -1, -119, 4, -1, 0, -1, 115, 2, -2, 0, 1, 107,
    ];

    /** Apparent geocentric Moon: RA, Dec (radians, true equinox of date) and distance in km. */
    function moonGeocentric(utcMs) {
        const T = centuries(utcMs), T2 = T * T, T3 = T2 * T, T4 = T3 * T;
        const Lp = (218.3164477 + 481267.88123421 * T - 0.0015786 * T2 + T3 / 538841 - T4 / 65194000) * DEG;
        const D = (297.8501921 + 445267.1114034 * T - 0.0018819 * T2 + T3 / 545868 - T4 / 113065000) * DEG;
        const M = (357.5291092 + 35999.0502909 * T - 0.0001536 * T2 + T3 / 24490000) * DEG;
        const Mp = (134.9633964 + 477198.8675055 * T + 0.0087414 * T2 + T3 / 69699 - T4 / 14712000) * DEG;
        const F = (93.2720950 + 483202.0175233 * T - 0.0036539 * T2 - T3 / 3526000 + T4 / 863310000) * DEG;
        const A1 = (119.75 + 131.849 * T) * DEG, A2 = (53.09 + 479264.290 * T) * DEG, A3 = (313.45 + 481266.484 * T) * DEG;
        const E = 1 - 0.002516 * T - 0.0000074 * T2, E2 = E * E;

        let sl = 0, sr = 0, sb = 0;
        for (let i = 0; i < MOON_LR.length; i += 6) {
            const m = MOON_LR[i + 1];
            const arg = MOON_LR[i] * D + m * M + MOON_LR[i + 2] * Mp + MOON_LR[i + 3] * F;
            const e = m === 0 ? 1 : (m === 1 || m === -1) ? E : E2;
            sl += MOON_LR[i + 4] * e * sin(arg);
            sr += MOON_LR[i + 5] * e * cos(arg);
        }
        for (let i = 0; i < MOON_B.length; i += 5) {
            const m = MOON_B[i + 1];
            const arg = MOON_B[i] * D + m * M + MOON_B[i + 2] * Mp + MOON_B[i + 3] * F;
            const e = m === 0 ? 1 : (m === 1 || m === -1) ? E : E2;
            sb += MOON_B[i + 4] * e * sin(arg);
        }
        sl += 3958 * sin(A1) + 1962 * sin(Lp - F) + 318 * sin(A2);
        sb += -2235 * sin(Lp) + 382 * sin(A3) + 175 * sin(A1 - F) + 175 * sin(A1 + F)
            + 127 * sin(Lp - Mp) - 115 * sin(Lp + Mp);

        const n = nutation(T);
        const lam = Lp + sl * 1e-6 * DEG + n.dpsi;
        const bet = sb * 1e-6 * DEG;
        const dist = 385000.56 + sr / 1000;
        const ce = cos(n.eps), se = sin(n.eps);
        const ra = Math.atan2(sin(lam) * ce - Math.tan(bet) * se, cos(lam));
        const dec = Math.asin(clamp1(sin(bet) * ce + cos(bet) * se * sin(lam)));
        return { ra: mod(ra, TAU), dec, dist, nut: n };
    }

    /** Moon as seen from the observer (parallax applied): unit vector in the true equatorial frame. */
    function moonTopocentricVector(utcMs, lat, lon) {
        const m = moonGeocentric(utcMs);
        const lst = gast(utcMs, m.nut) + lon * DEG;
        const latRad = lat * DEG;
        // Observer's geocentric position on the WGS84 ellipsoid, in km
        const f = 1 / 298.257223563, a = 6378.137;
        const C = 1 / Math.sqrt(cos(latRad) * cos(latRad) + (1 - f) * (1 - f) * sin(latRad) * sin(latRad));
        const S = (1 - f) * (1 - f) * C;
        const ox = a * C * cos(latRad) * cos(lst), oy = a * C * cos(latRad) * sin(lst), oz = a * S * sin(latRad);
        const cd = cos(m.dec);
        const x = m.dist * cd * cos(m.ra) - ox, y = m.dist * cd * sin(m.ra) - oy, z = m.dist * sin(m.dec) - oz;
        const r = Math.sqrt(x * x + y * y + z * z);
        return { x: x / r, y: y / r, z: z / r, lst, nut: m.nut };
    }

    /** Altitude and azimuth of the Moon for the observer. */
    function moonAltAz(utcMs, lat, lon) {
        const v = moonTopocentricVector(utcMs, lat, lon);
        return horizontal(Math.atan2(v.y, v.x), Math.asin(clamp1(v.z)), v.lst, lat * DEG);
    }

    /**
     * Angular separation (degrees) between the Moon and each object at one moment.
     * objects: array of { ra, dec } (hours, degrees). Returns a Float64Array.
     */
    function moonSeparations(objects, utcMs, lat, lon, frame) {
        const f = frame || frameFor(utcMs);
        const v = moonTopocentricVector(utcMs, lat, lon);
        const out = new Float64Array(objects.length);
        for (let i = 0; i < objects.length; i++) {
            const p = apparent(objects[i].ra, objects[i].dec, f);
            const cd = cos(p.dec);
            const dot = cd * cos(p.ra) * v.x + cd * sin(p.ra) * v.y + sin(p.dec) * v.z;
            out[i] = Math.acos(clamp1(dot)) / DEG;
        }
        return out;
    }

    function moonSeparation(raHours, decDeg, utcMs, lat, lon, frame) {
        return moonSeparations([{ ra: raHours, dec: decDeg }], utcMs, lat, lon, frame)[0];
    }

    /** Illuminated fraction of the Moon in percent, one decimal, at 12:00 UTC of the date. */
    function moonPhase(dateStr) {
        const d = parseDate(dateStr);
        const t = Date.UTC(d.year, d.month - 1, d.day, 12, 0, 0);
        const m = moonGeocentric(t), s = sunPosition(t);
        const cosPsi = clamp1(sin(s.dec) * sin(m.dec) + cos(s.dec) * cos(m.dec) * cos(s.ra - m.ra));
        const psi = Math.acos(cosPsi);
        const R = s.dist * 149597870.7;
        const i = Math.atan2(R * sin(psi), m.dist - R * cosPsi);
        return Math.round((1 + cos(i)) / 2 * 1000) / 10;
    }

    // ------------------------------------------------------------------ horizon mask

    function buildHorizonProfile(mask, floorAlt) {
        if (!mask || !mask.length) return null;
        const pts = mask.map(p => [p[0], p[1] < floorAlt ? floorAlt : p[1]]).sort((a, b) => a[0] - b[0]);
        const profile = [[0, floorAlt], [pts[0][0] - 0.001, floorAlt]];
        for (const p of pts) profile.push(p);
        profile.push([pts[pts.length - 1][0] + 0.001, floorAlt], [360, floorAlt]);
        return profile;
    }

    function profileAltitude(az, profile, floorAlt) {
        for (let i = 0; i < profile.length - 1; i++) {
            const a = profile[i], b = profile[i + 1];
            if (a[0] <= az && az <= b[0]) {
                if (Math.abs(b[0] - a[0]) < 1e-9) return a[1];
                return a[1] + (b[1] - a[1]) * ((az - a[0]) / (b[0] - a[0]));
            }
        }
        return floorAlt;
    }

    /** Minimum altitude at an azimuth for a horizon mask [[az, alt], ...] (interpolate_horizon). */
    function interpolateHorizon(az, mask, floorAlt) {
        const profile = buildHorizonProfile(mask, floorAlt);
        return profile ? profileAltitude(az, profile, floorAlt) : floorAlt;
    }

    // ------------------------------------------------------------------ observable duration

    const NONE = { minutes: 0, maxAltitude: 0, from: null, to: null };

    /**
     * The dusk-to-dawn sample times for a night, following
     * calculate_observable_duration_vectorized() including its fallbacks for nights
     * without astronomical darkness. Build once per night and share across objects.
     */
    function nightWindow(dateStr, tz, lat, lon, intervalMinutes, events) {
        const ev = events || sunEvents(dateStr, tz, lat, lon);
        const d = parseDate(dateStr);
        const valid = s => !!s && s !== 'N/A';
        const at = (hm, dayOffset) =>
            localToUtc(d.year, d.month, d.day + (dayOffset || 0), +hm.slice(0, 2), +hm.slice(3, 5), tz);

        let duskStr = ev.astronomical_dusk, dawnStr = ev.astronomical_dawn, noAstroNight = false;
        if (!valid(duskStr) && !valid(dawnStr)) {
            if (!valid(ev.sunset) || !valid(ev.sunrise)) return null;
            noAstroNight = true;
            duskStr = ev.sunset; dawnStr = ev.sunrise;
        } else if (!valid(duskStr)) {
            if (!valid(ev.sunset)) return null;
            duskStr = ev.sunset;
        } else if (!valid(dawnStr)) {
            if (!valid(ev.sunrise)) return null;
            dawnStr = ev.sunrise;
        }

        const dusk = at(duskStr);
        let dawn = at(dawnStr);
        if (dawn <= dusk) dawn = at(dawnStr, 1);

        const step = (intervalMinutes || 15) * 60000;
        const n = Math.floor((dawn - dusk) / step) + 1;
        const times = new Float64Array(n);
        for (let i = 0; i < n; i++) times[i] = dusk + i * step;
        return { times, dusk, dawn, noAstroNight, interval: intervalMinutes || 15, frame: frameFor(times[n >> 1]) };
    }

    /**
     * Observable minutes tonight, true maximum altitude during the night, and the first
     * and last observable sample (UTC ms or null).
     * opts: { threshold, interval, mask, window, events }
     */
    function observableDuration(raHours, decDeg, lat, lon, dateStr, tz, opts) {
        const o = opts || {};
        const threshold = o.threshold === undefined ? 20 : o.threshold;
        const w = o.window || nightWindow(dateStr, tz, lat, lon, o.interval, o.events);
        if (!w || !w.times.length) return NONE;

        const curve = altAzCurve(raHours, decDeg, lat, lon, w.times, w.frame);
        const profile = o.mask && o.mask.length > 1 ? buildHorizonProfile(o.mask, threshold) : null;

        let count = 0, first = -1, last = -1, maxAlt = -Infinity;
        for (let i = 0; i < w.times.length; i++) {
            const alt = curve.alt[i];
            if (alt > maxAlt) maxAlt = alt;
            const min = profile ? profileAltitude(curve.az[i], profile, threshold) : threshold;
            if (alt >= min) {
                count++;
                if (first < 0) first = i;
                last = i;
            }
        }
        return {
            minutes: w.noAstroNight ? 0 : count * w.interval,
            maxAltitude: maxAlt,
            from: first < 0 ? null : w.times[first],
            to: last < 0 ? null : w.times[last],
        };
    }

    return {
        // time
        localParts, localToUtc, formatHM, observingDate, timeGrid,
        // positions
        frameFor, apparent, altAz, altAzCurve, sunPosition, gast,
        // sun and moon
        sunEvents, moonPhase, moonAltAz, moonSeparation, moonSeparations, moonGeocentric,
        // night planning
        interpolateHorizon, nightWindow, observableDuration,
        SUN_RISE_SET_ALT, ASTRO_TWILIGHT_ALT,
    };
});
