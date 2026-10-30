/*
 * nova_solar_calendar.js — the Solar System page's Calendar tab, built in the browser.
 *
 * The planets' best-placed moments over the coming months: the next oppositions of
 * Mars, Jupiter, Saturn, Uranus and Neptune, and the greatest eastern (evening) and
 * western (morning) elongations of Mercury and Venus. No Moon, no Sun, no conjunctions.
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
 *     sampling_interval minutes). muted: maxAlt below altitude_threshold. Never dropped.
 *   - daysFromNow is the event's night minus the current night, in days: negative for
 *     past events, 0 tonight.
 *   - mag, diameterArcsec and elongationDeg (Mercury and Venus only, else null; always
 *     positive) are those of the event's instant.
 *
 * Returns { date, window: { start, end }, threshold, rows: [{ body, kind, utcMs, night,
 * hm, hmDay, daysFromNow, mag, diameterArcsec, elongationDeg, maxAlt, muted }] }, the
 * rows in time order. Throws on a missing location, a non-finite coordinate or any
 * engine failure; the page shows its error instead.
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
    const DAY_MS = 86400000;
    const DAYS_BACK = 30;
    const MONTHS_AHEAD = 24;
    const GOLDEN = 0.618034;

    const OPPOSITION_BODIES = Object.freeze(['mars', 'jupiter', 'saturn', 'uranus', 'neptune']);
    const ELONGATION_BODIES = Object.freeze(['mercury', 'venus']);
    const BODIES = Object.freeze(ELONGATION_BODIES.concat(OPPOSITION_BODIES));

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
        for (const body of BODIES) {
            for (const ev of search(body, win.start - 2 * DAY_MS, win.end + 2 * DAY_MS)) {
                if (ev.utcMs >= win.start && ev.utcMs <= win.end) found.push({ body: body, kind: ev.kind, utcMs: ev.utcMs });
            }
        }
        found.sort((x, y) => x.utcMs - y.utcMs);

        const sun = {};
        const rows = found.map(ev => {
            const wall = A.localParts(ev.utcMs, s.tz);
            const night = W.observingNight(wall);
            const nd = parseDate(night);
            const nw = N.nightWindow(night, s, A, W, sun);
            const maxAlt = N.maxAltitude(ev.body, N.samples(nw.start, nw.end, s.interval), s, P);
            const det = P.details(ev.body, ev.utcMs);
            return {
                body: ev.body,
                kind: ev.kind,
                utcMs: ev.utcMs,
                night: night,
                hm: A.formatHM(ev.utcMs, s.tz),
                hmDay: Math.round(dayNumber(wall) - dayNumber(nd)),
                daysFromNow: Math.round(dayNumber(nd) - today),
                mag: finite(det.mag, ev.body + ' magnitude'),
                diameterArcsec: finite(det.diameterArcsec, ev.body + ' diameter'),
                elongationDeg: ELONGATION_BODIES.indexOf(ev.body) >= 0 ? Math.abs(det.elongationDeg) : null,
                maxAlt: maxAlt,
                muted: maxAlt < s.threshold,
            };
        });

        return { date: date, window: win, threshold: s.threshold, rows: rows };
    }

    return {
        BODIES: BODIES, OPPOSITION_BODIES: OPPOSITION_BODIES, ELONGATION_BODIES: ELONGATION_BODIES,
        search: search, events: events,
    };
});
