/*
 * nova_transit.js — the meridian transit of an object on one observing night.
 *
 * Port of calculate_transit_time() in nova_DSO_tracker's modules/astro_calculations.py
 * (checked against Nova 6.9.1 beta), so the app can show the same transit time as Nova
 * and mark it on the altitude chart. It answers the question the Python function
 * answers, with the same rules and the same values, including the ones that look wrong:
 *
 *   - The window is 24 absolute hours from local noon on the given night, not dusk to
 *     dawn. On a DST night it therefore ends at 13:00 wall clock the next day.
 *   - Both crossings are computed, the upper transit (next_transit) and the lower one
 *     (next_antitransit, the nadir). Either can be the one reported.
 *   - A crossing is kept only if its time of day lies between astronomical dusk and
 *     astronomical dawn. Nova compares the crossing's clock time with the twilights
 *     truncated to the minute, so a crossing in the first minute after dawn counts as
 *     daytime and is dropped; that is reproduced here, not corrected.
 *   - If no crossing falls in the night, or the night has no astronomical twilight at
 *     all, the upper transit is returned even when it is in broad daylight.
 *   - Circumpolar and never-rising objects get a time like any other: ephem's transit
 *     calls do not raise for them, so Nova's AlwaysUpError/NeverUpError handler is dead
 *     code. An object that never rises is given the time it crosses the meridian below
 *     the horizon.
 *   - Seconds are truncated, never rounded, so the printed minute can be up to 59.9
 *     seconds earlier than the instant it names.
 *
 * The transit instant is the moment the object's apparent place of date has hour angle
 * zero, which is what ephem solves for. Refraction and the observer's pressure do not
 * enter it (checked: pressure 1010 and 0 give the same instant), and the observer's
 * J2000 epoch only affects the coordinates ephem reports, not the time. So the answer
 * needs the same precession, nutation and annual aberration the rest of the engine
 * uses, which is NovaAstro.apparent() with NovaAstro.frameFor() and NovaAstro.gast().
 *
 * Depends on the global NovaAstro (src/nova_astro.js), loaded as a plain script first.
 * Everything is a pure function of its arguments: no DOM, no network, no browser API,
 * and the module runs unchanged in Node, which is how tools/compare_transit.cjs checks
 * it against tests/fixtures/transit_reference.json.
 *
 * transit(raHours, decDeg, lat, lon, tz, dateStr)
 *     raHours, decDeg  J2000 position, as the library stores it.
 *     lat, lon         degrees, east positive.
 *     tz               IANA name.
 *     dateStr          'YYYY-MM-DD', the observing night.
 *     Returns {
 *       display    Nova's string: 'HH:MM', 'HH:MM, HH:MM' (both crossings at night,
 *                  chronological), or 'N/A'.
 *       utcMs      the instant display names, UTC milliseconds, or null; the first
 *                  one when display names two.
 *       utcMsAll   every instant display names, chronological.
 *       kinds      'upper' or 'lower' for each of utcMsAll, in the same order.
 *       upper      the window's upper transit, UTC milliseconds, or null if the window
 *                  has none. This is the crossing the chart marks.
 *       lower      the window's lower transit, UTC milliseconds, or null.
 *       crossings  both crossings in the window, chronological:
 *                  [{ utcMs, kind, inNight }], where inNight is Nova's dusk-to-dawn
 *                  test for that crossing.
 *       window     { start, end }, UTC milliseconds: local noon to local noon.
 *     }
 *
 * crossings(raHours, decDeg, lat, lon, tz, dateStr) returns the same crossings array
 * with the window, without the display logic. transit() is built on it.
 *
 * One deliberate difference: Nova hands non-finite coordinates to ephem and gets a
 * meaningless time back (a NaN right ascension is reported as 06:04 for Vienna). The
 * library never stores such a value, so the module returns the 'N/A' result instead of
 * reproducing that. Non-finite lat/lon/tz/date are an error, not a value: they throw.
 */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.NovaTransit = factory();
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const TAU = 2 * Math.PI;
    const DEG = Math.PI / 180;
    // A mean sidereal day, the step the search takes before refining. ephem works in
    // sidereal time, so this is the period of the hour angle, not 86400000 ms.
    const SIDEREAL_DAY_MS = 86164090.5;
    const DAY_MS = 86400000;

    const mod = (a, n) => ((a % n) + n) % n;
    const plusMinusPi = a => mod(a - Math.PI, TAU) - Math.PI;

    // Nova's SUN_EVENTS_CACHE, for the same reason: one screen asks about the same
    // night and place once per object.
    const sunCache = {};

    // NovaAstro is a global from the script loaded before this one; looked up at call
    // time, like nova_upnow.js does it, so the order of the two files is all that matters.
    function engine() {
        const A = typeof NovaAstro !== 'undefined' ? NovaAstro : null;
        if (!A) {
            throw new Error('nova_transit: NovaAstro is not loaded (src/nova_astro.js must come first)');
        }
        return A;
    }

    function parseDate(dateStr) {
        const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(dateStr));
        if (!m) throw new Error('nova_transit: dateStr must be YYYY-MM-DD, got ' + JSON.stringify(dateStr));
        return { year: +m[1], month: +m[2], day: +m[3] };
    }

    // The twilight events for a night, as Nova's calculate_sun_events_cached returns
    // them: 'HH:MM' or 'N/A', plus the instants.
    function sunEvents(dateStr, tz, lat, lon) {
        const key = dateStr + '|' + tz + '|' + lat + '|' + lon;
        let ev = sunCache[key];
        if (!ev) ev = sunCache[key] = engine().sunEvents(dateStr, tz, lat, lon);
        return ev;
    }

    // 'HH:MM' as the seconds Nova's datetime.strptime(dusk_str, "%H:%M").time() gives:
    // the minute is exact and the seconds are zero.
    function clockSeconds(hhmm) {
        const m = /^(\d{1,2}):(\d{2})$/.exec(hhmm);
        return m ? (+m[1]) * 3600 + (+m[2]) * 60 : null;
    }

    // A crossing's own time of day, seconds and fraction, as datetime.time() carries it.
    function daySeconds(utcMs, tz) {
        const p = engine().localParts(utcMs, tz);
        return p.hour * 3600 + p.minute * 60 + p.second + mod(utcMs, 1000) / 1000;
    }

    // Nova's test for one crossing: dusk > dawn spans midnight.
    function inNight(sec, duskSec, dawnSec) {
        return duskSec > dawnSec ? (sec >= duskSec || sec <= dawnSec)
                                 : (duskSec <= sec && sec <= dawnSec);
    }

    // The hour angle of the object's apparent place at one moment, radians in [0, 2pi).
    function hourAngle(raHours, decDeg, lat, lon, utcMs) {
        const A = engine();
        const frame = A.frameFor(utcMs);
        const p = A.apparent(raHours, decDeg, frame);
        return mod(A.gast(utcMs, frame.nut) + lon * DEG - p.ra, TAU);
    }

    // The first moment at or after `from` at which the hour angle equals target
    // (0 for the upper transit, pi for the lower), the same crossing ephem's
    // next_transit and next_antitransit return. The first step jumps forward by the
    // sidereal fraction of a turn, the rest refine with the signed difference, which
    // converges to well under a millisecond.
    function findCrossing(raHours, decDeg, lat, lon, from, target) {
        let t = from;
        for (let i = 0; i < 8; i++) {
            const ha = hourAngle(raHours, decDeg, lat, lon, t);
            const bump = (i === 0 ? mod(target - ha, TAU) : plusMinusPi(target - ha)) / TAU;
            t += bump * SIDEREAL_DAY_MS;
            if (Math.abs(bump * SIDEREAL_DAY_MS) < 1) break;
        }
        return t;
    }

    // Everything the two entry points need: the window, both crossings with Nova's
    // night flag, and the twilights they were tested against.
    function nightOf(raHours, decDeg, lat, lon, tz, dateStr) {
        const A = engine();
        const d = parseDate(dateStr);
        const start = A.localToUtc(d.year, d.month, d.day, 12, 0, tz);
        const end = start + DAY_MS;

        const ev = sunEvents(dateStr, tz, lat, lon);
        const duskSec = ev.astronomical_dusk === 'N/A' ? null : clockSeconds(ev.astronomical_dusk);
        const dawnSec = ev.astronomical_dawn === 'N/A' ? null : clockSeconds(ev.astronomical_dawn);
        const hasNight = duskSec !== null && dawnSec !== null;

        const list = [];
        const upper = findCrossing(raHours, decDeg, lat, lon, start, 0);
        if (upper >= start && upper < end) list.push({ utcMs: upper, kind: 'upper' });
        const lower = findCrossing(raHours, decDeg, lat, lon, start, Math.PI);
        if (lower >= start && lower < end) list.push({ utcMs: lower, kind: 'lower' });
        list.sort((a, b) => a.utcMs - b.utcMs);
        for (const c of list) {
            c.inNight = hasNight && inNight(daySeconds(c.utcMs, tz), duskSec, dawnSec);
        }

        const at = kind => {
            const hit = list.find(c => c.kind === kind);
            return hit ? hit.utcMs : null;
        };
        return {
            window: { start: start, end: end },
            crossings: list,
            upper: at('upper'),
            lower: at('lower'),
            night: hasNight ? list.filter(c => c.inNight) : [],
        };
    }

    // Non-finite coordinates are not a case the library can produce; see the note in
    // the header. Nova's own junk answer for them is not reproduced.
    function usable(raHours, decDeg) {
        return isFinite(raHours) && isFinite(decDeg);
    }

    function nothing() {
        return { display: 'N/A', utcMs: null, utcMsAll: [], kinds: [],
                 upper: null, lower: null, crossings: [], window: null };
    }

    function transit(raHours, decDeg, lat, lon, tz, dateStr) {
        const A = engine();
        if (!usable(raHours, decDeg)) {
            const empty = nothing();
            const d = parseDate(dateStr);
            empty.window = { start: A.localToUtc(d.year, d.month, d.day, 12, 0, tz),
                             end: A.localToUtc(d.year, d.month, d.day, 12, 0, tz) + DAY_MS };
            return empty;
        }

        const n = nightOf(raHours, decDeg, lat, lon, tz, dateStr);
        const named = n.night.length ? n.night : (n.upper !== null ? [{ utcMs: n.upper, kind: 'upper' }] : []);

        return {
            display: named.length ? named.map(c => A.formatHM(c.utcMs, tz)).join(', ') : 'N/A',
            utcMs: named.length ? named[0].utcMs : null,
            utcMsAll: named.map(c => c.utcMs),
            kinds: named.map(c => c.kind),
            upper: n.upper,
            lower: n.lower,
            crossings: n.crossings,
            window: n.window,
        };
    }

    // The crossings alone, for callers that mark them without wanting the string.
    function crossings(raHours, decDeg, lat, lon, tz, dateStr) {
        engine();
        if (!usable(raHours, decDeg)) return { window: null, crossings: [], upper: null, lower: null };
        const n = nightOf(raHours, decDeg, lat, lon, tz, dateStr);
        return { window: n.window, crossings: n.crossings, upper: n.upper, lower: n.lower };
    }

    return { transit: transit, crossings: crossings };
});
