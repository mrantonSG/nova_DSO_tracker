/*
 * nova_transit.js — the meridian transit of an object on one observing night.
 *
 * Port of calculate_transit_time() in nova_DSO_tracker's modules/astro_calculations.py
 * (checked against Nova 7.3.1 with the November 2026 upper-transit fix), so the app
 * can show the same transit time as Nova and mark it on the altitude chart. It answers
 * the question the Python function answers, with the same rules and the same values:
 *
 *   - The window is 24 absolute hours from local noon on the given night, not dusk to
 *     dawn. On a DST night it therefore ends at 13:00 wall clock the next day.
 *   - Only the upper transit (ephem's next_transit, the upper culmination) is ever
 *     reported, daylight included. The lower one (next_antitransit) is still computed
 *     and returned as `lower` and in `crossings`, but it never names the display:
 *     it is not a meridian flip. Until November 2026 both reported a lower crossing at
 *     night instead of a daytime upper one; that rule is gone from both.
 *   - An object that never rises, whose upper culmination 90 - |lat - dec| (the
 *     catalogue position, as Nova's Max Altitude column has it) is at or below 0, gets
 *     'N/A'. `upper` and `lower` are still filled in for it.
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
 *       display    Nova's string: 'HH:MM' (the upper transit) or 'N/A'.
 *       utcMs      the instant display names, UTC milliseconds, or null.
 *       utcMsAll   [utcMs], or [] when display is 'N/A'. Kept for older callers.
 *       kinds      ['upper'], or [] when display is 'N/A'. Kept for older callers.
 *       upper      the window's upper transit, UTC milliseconds, or null if the window
 *                  has none. This is the crossing the chart marks.
 *       lower      the window's lower transit, UTC milliseconds, or null.
 *       crossings  both crossings in the window, chronological:
 *                  [{ utcMs, kind, inNight }], where inNight says whether the
 *                  crossing's clock time lies between astronomical dusk and dawn
 *                  (twilights truncated to the minute, as Nova printed them).
 *                  Informational only: it no longer decides the display.
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

    // Whether a clock time lies between dusk and dawn: dusk > dawn spans midnight.
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

    // Everything the two entry points need: the window and both crossings, each with
    // its dusk-to-dawn flag.
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
        };
    }

    // Non-finite coordinates are not a case the library can produce; see the note in
    // the header. Nova's own junk answer for them is not reproduced.
    function usable(raHours, decDeg) {
        return isFinite(raHours) && isFinite(decDeg);
    }

    // Nova's never-rises test: the upper culmination of the catalogue position,
    // 90 - |lat - dec|, at or below the horizon.
    function rises(decDeg, lat) {
        return 90 - Math.abs(lat - decDeg) > 0;
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
        // Only ever the upper transit; utcMsAll and kinds stay arrays for older callers.
        const named = rises(decDeg, lat) && n.upper !== null ? [{ utcMs: n.upper, kind: 'upper' }] : [];

        return {
            display: named.length ? A.formatHM(named[0].utcMs, tz) : 'N/A',
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
