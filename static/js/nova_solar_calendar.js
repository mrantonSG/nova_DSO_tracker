/*
 * nova_solar_calendar.js — the Solar System page's Calendar tab, built in the browser.
 *
 * The planets' best-placed moments over the coming months: the next oppositions of
 * Mars, Jupiter, Saturn, Uranus and Neptune, the greatest eastern (evening) and
 * western (morning) elongations of Mercury and Venus, and the conjunctions of two
 * planets or of the Moon and a bright planet. No Sun, no occultations.
 * No DOM: the page (static/js/solar_system.js) renders the rows and formats the numbers.
 *
 * Built on the shared engines and the Bodies tab's own rules, looked up as globals at
 * call time, so the page only has to load them first:
 *   NovaAstro          (static/js/nova_astro.js)           time zones
 *   NovaPlanets        (static/js/nova_planets.js)         the bodies
 *   NovaDashboardRows  (static/js/nova_dashboard_rows.js)  the instant and the night
 *   NovaSolarRows      (static/js/nova_solar_rows.js)      night window and its maximum
 *
 * search(body, startUtcMs, endUtcMs) -> [{ kind, utcMs }] in time order, geocentric:
 *   - 'opposition' (outer planets): the moment the planet's and the Sun's ecliptic
 *     longitudes differ by 180 deg, found where NovaPlanets.details().elongationDeg
 *     changes sign with |elongation| > 90 deg on both sides (its sign is the side of
 *     the longitude difference), narrowed to a minute by bisection.
 *   - 'elongationEast' / 'elongationWest' (Mercury, Venus): a maximum of |elongation|,
 *     narrowed to a minute by golden section; the side is the sign at that moment.
 *   Both start from one sample a day; events closer than that cannot occur.
 *   Accuracy: within 0.4 h of ephem 4.2 for 2024-2034, checked by
 *   tests/test_solar_calendar.py. Verified for that span only; the engine's elements
 *   are fitted for 1800-2050.
 *
 * searchPair(a, b, startUtcMs, endUtcMs, opts) -> [{ kind: 'conjunction', utcMs, sepDeg }]
 *   in time order: the minima of the two bodies' angular separation below opts.limitDeg
 *   (default the pair's limit, PLANET_LIMIT_DEG or MOON_LIMIT_DEG), narrowed to a minute
 *   by golden section. The pairs (PAIRS, either order): any two of CONJUNCTION_PLANETS,
 *   geocentric, from NovaPlanets.position() (the same J2000 frame for both, so the
 *   separation does not depend on it), one sample a day; and the Moon with one of
 *   MOON_PARTNERS, topocentric for opts.lat/opts.lon, from NovaPlanets.altAz() (diurnal
 *   parallax, no refraction: the Moon's parallax is as large as its limit), one sample
 *   every 3 hours.
 *
 * closestApproaches(startUtcMs, endUtcMs) -> [{ kind: 'closestApproach', utcMs, distAU }]:
 *   the minima of Mars's distance (NovaPlanets.position().distAU), one sample a day,
 *   then golden section to a minute. Not a row of its own: events() puts the one
 *   nearest each Mars opposition (within 60 days) on that row.
 *
 * events(opts) -> the table for one location and one observing night.
 * opts:
 *   inputs    the /api/desktop_inputs response for the location (only .location is read:
 *             lat, lon, timezone, altitude_threshold, sampling_interval)
 *   nowMs     the current instant, UTC milliseconds (Date.now())
 *   simDate   the simulation date 'YYYY-MM-DD', or null/'' for live
 *
 * The rules:
 *   - The instant and the current observing night are the Bodies tab's
 *     (NovaDashboardRows._wall: effectiveInstant, observingNight).
 *   - The window is local noon 30 days before the night's date to local noon on the
 *     night's date 24 months later. Events outside it are dropped.
 *   - Each event belongs to an observing night: its local date, or the day before when
 *     its local hour is before 12. `night` is that date (the table's Date column); `hm`
 *     is the event's local time 'HH:MM' and `hmDay` its calendar day relative to the
 *     night (0 that evening, +1 the next morning), for NovaSolarRows.timeLabel(); the
 *     page shows them in the Date cell's tooltip, not in the cell.
 *   - maxAlt is the body's highest altitude over that night's window, the Bodies tab's
 *     rule (sunset to sunrise, noon to noon when the Sun does not set or rise, every
 *     sampling_interval minutes); for a conjunction the higher of the two bodies'.
 *     muted: maxAlt below altitude_threshold, mutedReason 'altitude'. A conjunction is
 *     also muted when it is closer to the Sun than SUN_MIN_DEG, mutedReason 'sun' (which
 *     wins when both apply). Never dropped; mutedReason is null when not muted.
 *   - daysFromNow is the event's night minus the current night, in days: negative for
 *     past events, 0 tonight.
 *   - mag, diameterArcsec and elongationDeg (Mercury and Venus only, else null; always
 *     positive) are those of the event's instant.
 *   - A conjunction row: `bodies` is [brighter, other] (by magnitude at the instant),
 *     `body` the brighter (the row's click target), mag and diameterArcsec the
 *     brighter's, `other` { body, mag, diameterArcsec }, sepDeg the separation and
 *     elongationDeg the smaller of the two bodies' (always given). Other rows: bodies
 *     [body], other and sepDeg null.
 *   - ringTiltDeg: Saturn's ring tilt at the instant on every row Saturn is in, else null.
 *   - closestApproach: on Mars's opposition rows { utcMs, night, distAU } (night by the
 *     same noon rule), else null.
 *
 * Returns { date, window: { start, end }, threshold, rows: [{ body, bodies, kind, utcMs,
 * night, hm, hmDay, daysFromNow, mag, diameterArcsec, elongationDeg, maxAlt, muted,
 * mutedReason, sepDeg, other, ringTiltDeg, closestApproach }] }, the rows in time order.
 * Throws on a missing location, a non-finite coordinate or any engine failure; the page
 * shows its error instead.
 */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.NovaSolarCalendar = factory();
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const MINUTE_MS = 60000;
    const HOUR_MS = 3600000;
    const DAY_MS = 86400000;
    const DAYS_BACK = 30;
    const MONTHS_AHEAD = 24;
    const GOLDEN = 0.618034;

    const OPPOSITION_BODIES = Object.freeze(['mars', 'jupiter', 'saturn', 'uranus', 'neptune']);
    const ELONGATION_BODIES = Object.freeze(['mercury', 'venus']);
    const BODIES = Object.freeze(ELONGATION_BODIES.concat(OPPOSITION_BODIES));

    const CONJUNCTION_PLANETS = Object.freeze(['mercury', 'venus', 'mars', 'jupiter', 'saturn', 'uranus', 'neptune']);
    const MOON_PARTNERS = Object.freeze(['venus', 'mars', 'jupiter', 'saturn']);
    const PLANET_LIMIT_DEG = 2;
    const MOON_LIMIT_DEG = 1;
    const SUN_MIN_DEG = 15;
    const MOON_STEP_MS = 3 * HOUR_MS;
    const CLOSEST_MARGIN_MS = 60 * DAY_MS;
    const PAIRS = Object.freeze((function () {
        const out = [];
        CONJUNCTION_PLANETS.forEach((a, i) => CONJUNCTION_PLANETS.slice(i + 1).forEach(b => out.push(Object.freeze([a, b]))));
        MOON_PARTNERS.forEach(p => out.push(Object.freeze(['moon', p])));
        return out;
    })());

    function engine() {
        const A = typeof NovaAstro !== 'undefined' ? NovaAstro : null;
        const P = typeof NovaPlanets !== 'undefined' ? NovaPlanets : null;
        const R = typeof NovaDashboardRows !== 'undefined' ? NovaDashboardRows : null;
        const S = typeof NovaSolarRows !== 'undefined' ? NovaSolarRows : null;
        if (!A || !P || !R || !S) {
            throw new Error('nova_solar_calendar: nova_astro.js, nova_planets.js, nova_dashboard_rows.js '
                + 'and nova_solar_rows.js must be loaded first');
        }
        return { A: A, P: P, W: R._wall, N: S._night };
    }

    function finite(x, what) {
        const v = typeof x === 'number' ? x : NaN;
        if (!isFinite(v)) throw new Error('nova_solar_calendar: ' + what + ' is not a finite number');
        return v;
    }

    function parseDate(dateStr) {
        const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(dateStr));
        if (!m) throw new Error('nova_solar_calendar: date must be YYYY-MM-DD, got ' + JSON.stringify(dateStr));
        return { year: +m[1], month: +m[2], day: +m[3] };
    }

    const dayNumber = d => Date.UTC(d.year, d.month - 1, d.day) / DAY_MS;

    // ------------------------------------------------------------------ search

    function oppositions(body, start, end, P) {
        const el = t => P.details(body, t).elongationDeg;
        const out = [];
        let t0 = start, e0 = el(t0);
        for (let t1 = start + DAY_MS; t1 <= end; t1 += DAY_MS) {
            const e1 = el(t1);
            if (Math.abs(e0) > 90 && Math.abs(e1) > 90 && (e0 < 0) !== (e1 < 0)) {
                let lo = t0, hi = t1;
                const westAtLo = e0 < 0;
                while (hi - lo > MINUTE_MS) {
                    const mid = (lo + hi) / 2;
                    if ((el(mid) < 0) === westAtLo) lo = mid; else hi = mid;
                }
                out.push({ kind: 'opposition', utcMs: Math.round((lo + hi) / 2) });
            }
            t0 = t1;
            e0 = e1;
        }
        return out;
    }

    function greatestElongations(body, start, end, P) {
        const f = t => Math.abs(P.details(body, t).elongationDeg);
        const out = [];
        let a = f(start), b = f(start + DAY_MS);
        for (let t = start + 2 * DAY_MS; t <= end; t += DAY_MS) {
            const c = f(t);
            if (b > a && b >= c) {
                let lo = t - 2 * DAY_MS, hi = t;
                while (hi - lo > MINUTE_MS) {
                    const m1 = hi - (hi - lo) * GOLDEN, m2 = lo + (hi - lo) * GOLDEN;
                    if (f(m1) > f(m2)) hi = m2; else lo = m1;
                }
                const utcMs = Math.round((lo + hi) / 2);
                const east = P.details(body, utcMs).elongationDeg >= 0;
                out.push({ kind: east ? 'elongationEast' : 'elongationWest', utcMs: utcMs });
            }
            a = b;
            b = c;
        }
        return out;
    }

    /** The local minima of f over [start, end], sampled every step, each narrowed to a
     *  minute by golden section: [{ utcMs, value }]. */
    function minima(f, start, end, step) {
        const out = [];
        let a = f(start), b = f(start + step);
        for (let t = start + 2 * step; t <= end; t += step) {
            const c = f(t);
            if (b < a && b <= c) {
                let lo = t - 2 * step, hi = t;
                while (hi - lo > MINUTE_MS) {
                    const m1 = hi - (hi - lo) * GOLDEN, m2 = lo + (hi - lo) * GOLDEN;
                    if (f(m1) < f(m2)) hi = m2; else lo = m1;
                }
                const utcMs = Math.round((lo + hi) / 2);
                out.push({ utcMs: utcMs, value: f(utcMs) });
            }
            a = b;
            b = c;
        }
        return out;
    }

    /** The angle between two directions given as longitude/latitude pairs, degrees (haversine). */
    function angleDeg(lon1, lat1, lon2, lat2) {
        const r = Math.PI / 180;
        const s = Math.sin((lat2 - lat1) * r / 2) ** 2
            + Math.cos(lat1 * r) * Math.cos(lat2 * r) * Math.sin((lon2 - lon1) * r / 2) ** 2;
        return 2 * Math.asin(Math.min(1, Math.sqrt(s))) / r;
    }

    function pairOf(a, b) {
        const p = PAIRS.find(x => (x[0] === a && x[1] === b) || (x[0] === b && x[1] === a));
        if (!p) throw new Error('nova_solar_calendar: no conjunctions for ' + JSON.stringify([a, b]));
        return p;
    }

    function searchPair(a, b, startUtcMs, endUtcMs, opts) {
        const P = engine().P;
        const pair = pairOf(a, b);
        const o = opts || {};
        const start = finite(startUtcMs, 'start'), end = finite(endUtcMs, 'end');
        const moon = pair[0] === 'moon';
        const limit = o.limitDeg === undefined ? (moon ? MOON_LIMIT_DEG : PLANET_LIMIT_DEG) : finite(o.limitDeg, 'limitDeg');
        if (!(end > start)) return [];
        let sep;
        if (moon) {
            const lat = finite(o.lat, 'lat'), lon = finite(o.lon, 'lon');
            sep = t => {
                const x = P.altAz(pair[0], t, lat, lon), y = P.altAz(pair[1], t, lat, lon);
                return angleDeg(x.az, x.alt, y.az, y.alt);
            };
        } else {
            sep = t => {
                const x = P.position(pair[0], t), y = P.position(pair[1], t);
                return angleDeg(x.raHours * 15, x.decDeg, y.raHours * 15, y.decDeg);
            };
        }
        return minima(sep, start, end, moon ? MOON_STEP_MS : DAY_MS)
            .filter(m => m.value < limit)
            .map(m => ({ kind: 'conjunction', utcMs: m.utcMs, sepDeg: m.value }));
    }

    function closestApproaches(startUtcMs, endUtcMs) {
        const P = engine().P;
        const start = finite(startUtcMs, 'start'), end = finite(endUtcMs, 'end');
        if (!(end > start)) return [];
        return minima(t => P.position('mars', t).distAU, start, end, DAY_MS)
            .map(m => ({ kind: 'closestApproach', utcMs: m.utcMs, distAU: m.value }));
    }

    function search(body, startUtcMs, endUtcMs) {
        const P = engine().P;
        if (BODIES.indexOf(body) < 0) throw new Error('nova_solar_calendar: no events for ' + JSON.stringify(body));
        const start = finite(startUtcMs, 'start'), end = finite(endUtcMs, 'end');
        if (!(end > start)) return [];
        return OPPOSITION_BODIES.indexOf(body) >= 0
            ? oppositions(body, start, end, P)
            : greatestElongations(body, start, end, P);
    }

    // ------------------------------------------------------------------ table

    function events(opts) {
        const o = opts || {};
        const E = engine(), A = E.A, P = E.P, W = E.W, N = E.N;
        const s = N.site(o.inputs);

        const now = W.effectiveInstant(finite(o.nowMs, 'nowMs'), o.simDate, s.tz, A);
        const date = W.observingNight(now.wall);
        const d = parseDate(date);
        const noonOf = dateStr => {
            const n = parseDate(dateStr);
            return W.localize(n.year, n.month, n.day, 12, 0, s.tz, A, false);
        };
        const win = {
            start: noonOf(N.dateString(d.year, d.month, d.day - DAYS_BACK)),
            end: noonOf(N.dateString(d.year, d.month + MONTHS_AHEAD, d.day)),
        };
        const today = dayNumber(d);

        // Two days of margin, so an event right at an edge is still bracketed by samples
        const found = [];
        const inWindow = ev => ev.utcMs >= win.start && ev.utcMs <= win.end;
        for (const body of BODIES) {
            for (const ev of search(body, win.start - 2 * DAY_MS, win.end + 2 * DAY_MS)) {
                if (inWindow(ev)) found.push({ bodies: [body], kind: ev.kind, utcMs: ev.utcMs });
            }
        }
        for (const pair of PAIRS) {
            const evs = searchPair(pair[0], pair[1], win.start - 2 * DAY_MS, win.end + 2 * DAY_MS, { lat: s.lat, lon: s.lon });
            for (const ev of evs) {
                if (inWindow(ev)) found.push({ bodies: pair.slice(), kind: ev.kind, utcMs: ev.utcMs, sepDeg: ev.sepDeg });
            }
        }
        found.sort((x, y) => x.utcMs - y.utcMs);

        const sun = {};
        const nightOf = utcMs => {
            const wall = A.localParts(utcMs, s.tz);
            return { wall: wall, night: W.observingNight(wall) };
        };
        const rows = found.map(ev => {
            const at = nightOf(ev.utcMs);
            const night = at.night;
            const nd = parseDate(night);
            const nw = N.nightWindow(night, s, A, W, sun);
            const times = N.samples(nw.start, nw.end, s.interval);
            const dets = ev.bodies.map(b => P.details(b, ev.utcMs));
            const conj = ev.kind === 'conjunction';
            // [brighter, other] for a conjunction, by magnitude at the instant
            const order = conj && dets[1].mag < dets[0].mag ? [1, 0] : [0, 1];
            const bodies = conj ? order.map(i => ev.bodies[i]) : ev.bodies.slice();
            const det = dets[order[0]];
            const body = bodies[0];
            const maxAlt = Math.max.apply(null, bodies.map(b => N.maxAltitude(b, times, s, P)));
            let elongationDeg = null;
            if (conj) elongationDeg = Math.min(Math.abs(dets[0].elongationDeg), Math.abs(dets[1].elongationDeg));
            else if (ELONGATION_BODIES.indexOf(body) >= 0) elongationDeg = Math.abs(det.elongationDeg);
            const tooClose = conj && elongationDeg < SUN_MIN_DEG;
            const low = maxAlt < s.threshold;
            const saturn = bodies.indexOf('saturn');

            let closest = null;
            if (body === 'mars' && ev.kind === 'opposition') {
                const near = closestApproaches(ev.utcMs - CLOSEST_MARGIN_MS, ev.utcMs + CLOSEST_MARGIN_MS);
                let best = null;
                for (const c of near) if (!best || Math.abs(c.utcMs - ev.utcMs) < Math.abs(best.utcMs - ev.utcMs)) best = c;
                if (best) closest = { utcMs: best.utcMs, night: nightOf(best.utcMs).night, distAU: best.distAU };
            }

            const other = conj ? dets[order[1]] : null;
            return {
                body: body,
                bodies: bodies,
                kind: ev.kind,
                utcMs: ev.utcMs,
                night: night,
                hm: A.formatHM(ev.utcMs, s.tz),
                hmDay: Math.round(dayNumber(at.wall) - dayNumber(nd)),
                daysFromNow: Math.round(dayNumber(nd) - today),
                mag: finite(det.mag, body + ' magnitude'),
                diameterArcsec: finite(det.diameterArcsec, body + ' diameter'),
                elongationDeg: elongationDeg,
                maxAlt: maxAlt,
                muted: low || tooClose,
                mutedReason: tooClose ? 'sun' : (low ? 'altitude' : null),
                sepDeg: conj ? ev.sepDeg : null,
                other: conj ? {
                    body: bodies[1],
                    mag: finite(other.mag, bodies[1] + ' magnitude'),
                    diameterArcsec: finite(other.diameterArcsec, bodies[1] + ' diameter'),
                } : null,
                ringTiltDeg: saturn >= 0 ? finite(dets[conj ? order[saturn] : 0].ringTiltDeg, 'saturn ring tilt') : null,
                closestApproach: closest,
            };
        });

        return { date: date, window: win, threshold: s.threshold, rows: rows };
    }

    return {
        BODIES: BODIES, OPPOSITION_BODIES: OPPOSITION_BODIES, ELONGATION_BODIES: ELONGATION_BODIES,
        CONJUNCTION_PLANETS: CONJUNCTION_PLANETS, MOON_PARTNERS: MOON_PARTNERS, PAIRS: PAIRS,
        PLANET_LIMIT_DEG: PLANET_LIMIT_DEG, MOON_LIMIT_DEG: MOON_LIMIT_DEG, SUN_MIN_DEG: SUN_MIN_DEG,
        search: search, searchPair: searchPair, closestApproaches: closestApproaches, events: events,
    };
});
