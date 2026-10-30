/*
 * nova_solar_rows.js — the Solar System page's bodies table, built in the browser.
 *
 * NovaSolarRows.rows(opts) returns one row per body of NovaPlanets.BODIES, always all
 * of them and in that order, for one location and one observing night. No DOM: the
 * page (static/js/solar_system.js) renders the rows and formats the numbers.
 *
 * Built on the shared engines and the dashboard's own wall-time rules, looked up as
 * globals at call time, so the page only has to load them first:
 *   NovaAstro          (static/js/nova_astro.js)           sun events, time zones
 *   NovaPlanets        (static/js/nova_planets.js)         the bodies
 *   NovaDashboardRows  (static/js/nova_dashboard_rows.js)  the instant and the night
 *
 * opts:
 *   inputs    the /api/desktop_inputs response for the location (only .location is read:
 *             lat, lon, timezone, altitude_threshold, sampling_interval)
 *   nowMs     the current instant, UTC milliseconds (Date.now())
 *   simDate   the simulation date 'YYYY-MM-DD', or null/'' for live
 *
 * The rules:
 *   - The instant and the observing night are the dashboard table's
 *     (NovaDashboardRows rows()): live the instant is nowMs; with simDate it is simDate
 *     at the current wall clock of the location's zone. The night is that wall date,
 *     or the day before when the wall hour is before 12, also in simulation.
 *   - Altitude, azimuth, magnitude, diameter, illumination and elongation are those of
 *     the instant.
 *   - The night window is sunset on the night's date to sunrise the next morning
 *     (NovaAstro.sunEvents, the Sun's centre at SUN_RISE_SET_ALT): planets and the Moon
 *     are imaged in twilight, so the astronomical night would mute Mercury and Venus
 *     almost always. When the Sun does not set or does not rise (polar day or night) it
 *     is local noon on the night's date to local noon the day after.
 *   - Max altitude tonight is the highest sample of the night window, every
 *     sampling_interval minutes. A body whose max altitude is below altitude_threshold
 *     is muted, never dropped. No horizon mask: the dashboard's Max Altitude has none.
 *   - Rise, transit and set are those of one up-interval (rise to set), taken from
 *     NovaPlanets.events() over local noon of the night's date -36 h to +36 h, wide
 *     enough to hold any interval that touches the night:
 *       the interval overlapping the night window, the one containing the night's
 *       midpoint when several do (and otherwise the one with the longest overlap).
 *     The transit is the upper transit inside that interval, with its altitude. A side
 *     of the interval beyond the search window is null. When the body rises and sets in
 *     the search window but no interval overlaps the night, the row is notUpTonight: no
 *     rise or set, and the transit is the one closest to the night's midpoint. With no
 *     rise or set at all the row is alwaysUp or neverUp, the transit chosen the same way
 *     (below the horizon for a body that never rises).
 *   - Times are 'HH:MM' in the location's zone (NovaAstro.formatHM), each with its day
 *     relative to the night's date: 0 that evening's date, +1 the next calendar day,
 *     -1 the day before (riseDay, transitDay, setDay). timeLabel() prints the suffix.
 *
 * nightlyMax({inputs, body, year, month}) is the month chart's series: for every
 * calendar day of the month, the body's highest altitude over that day's night window
 * (sunset to the next sunrise, or noon to noon as above), sampled every
 * sampling_interval minutes: { dates: ['YYYY-MM-DD', ...], max_alt: [deg, ...] }.
 *
 * Everything throws on a missing location, a non-finite coordinate or any engine
 * failure; the page shows its error instead.
 *
 * defaultBody(rows) is the body with the highest max altitude tonight, the first in
 * BODIES order on a tie; pickBody(rows, requested) is `requested` when it names a
 * body, else defaultBody(rows).
 */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.NovaSolarRows = factory();
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const MINUTE_MS = 60000;
    const HOUR_MS = 3600000;
    const DAY_MS = 86400000;
    const SEARCH_HOURS = 36;
    const pad2 = n => (n < 10 ? '0' : '') + n;

    function engine() {
        const A = typeof NovaAstro !== 'undefined' ? NovaAstro : null;
        const P = typeof NovaPlanets !== 'undefined' ? NovaPlanets : null;
        const R = typeof NovaDashboardRows !== 'undefined' ? NovaDashboardRows : null;
        if (!A || !P || !R) {
            throw new Error('nova_solar_rows: nova_astro.js, nova_planets.js and nova_dashboard_rows.js must be loaded first');
        }
        return { A: A, P: P, W: R._wall };
    }

    function finite(x, what) {
        const v = typeof x === 'number' ? x : NaN;
        if (!isFinite(v)) throw new Error('nova_solar_rows: ' + what + ' is not a finite number');
        return v;
    }

    function parseDate(dateStr) {
        const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(dateStr));
        if (!m) throw new Error('nova_solar_rows: date must be YYYY-MM-DD, got ' + JSON.stringify(dateStr));
        return { year: +m[1], month: +m[2], day: +m[3] };
    }

    function dateString(year, month, day) {
        const t = new Date(Date.UTC(year, month - 1, day));
        return String(t.getUTCFullYear()).padStart(4, '0') + '-' + pad2(t.getUTCMonth() + 1)
            + '-' + pad2(t.getUTCDate());
    }

    /** 'YYYY-MM-DD' one calendar day later. */
    function dayAfter(dateStr) {
        const d = parseDate(dateStr);
        return dateString(d.year, d.month, d.day + 1);
    }

    /** The location block, checked. */
    function site(inputs) {
        const loc = (inputs || {}).location;
        if (!loc) throw new Error('nova_solar_rows: no location');
        const interval = finite(loc.sampling_interval, 'sampling_interval');
        if (!(interval > 0)) throw new Error('nova_solar_rows: sampling_interval must be positive');
        return {
            lat: finite(loc.lat, 'lat'), lon: finite(loc.lon, 'lon'), tz: String(loc.timezone),
            threshold: finite(loc.altitude_threshold, 'altitude_threshold'), interval: interval,
        };
    }

    /** The sample times start..end, every interval minutes, end included when it falls on one. */
    function samples(start, end, interval) {
        const step = interval * MINUTE_MS;
        const times = [];
        for (let t = start; t <= end; t += step) times.push(t);
        return times;
    }

    /** Local noon on the night's date, as UTC ms. */
    function localNoon(date, tz, A, W) {
        const d = parseDate(date);
        return W.localize(d.year, d.month, d.day, 12, 0, tz, A, false);
    }

    /**
     * The night window of `date`: sunset to the next sunrise, or local noon to local noon
     * when either is missing. `sun` caches sunEvents() by date for a run over many days.
     */
    function nightWindow(date, s, A, W, sun) {
        const ev = d => {
            if (!sun) return A.sunEvents(d, s.tz, s.lat, s.lon);
            if (!sun[d]) sun[d] = A.sunEvents(d, s.tz, s.lat, s.lon);
            return sun[d];
        };
        const next = dayAfter(date);
        const set = ev(date).sunset_utc;
        const rise = ev(next).sunrise_utc;
        if (set !== null && set !== undefined && rise !== null && rise !== undefined && rise > set) {
            return { start: set, end: rise, kind: 'sun' };
        }
        return { start: localNoon(date, s.tz, A, W), end: localNoon(next, s.tz, A, W), kind: 'noon' };
    }

    function maxAltitude(body, times, s, P) {
        const curve = P.altAzCurve(body, times, s.lat, s.lon);
        let best = -Infinity;
        for (let k = 0; k < curve.alt.length; k++) if (curve.alt[k] > best) best = curve.alt[k];
        return finite(best, body + ' max altitude');
    }

    // ------------------------------------------------------------------ up-intervals

    /** events() as up-intervals: { rise, set (event or null), transits: [upper transits inside] }. */
    function upIntervals(found) {
        const out = [];
        let cur = found.upAtStart ? { rise: null, set: null, transits: [] } : null;
        for (const e of found.events) {
            if (e.kind === 'transit') {
                if (cur) cur.transits.push(e);
            } else if (e.kind === 'rise') {
                cur = { rise: e, set: null, transits: [] };
            } else if (e.kind === 'set') {
                if (!cur) cur = { rise: null, set: null, transits: [] };
                cur.set = e;
                out.push(cur);
                cur = null;
            }
        }
        if (cur) out.push(cur);
        return out;
    }

    const lo = iv => (iv.rise ? iv.rise.utcMs : -Infinity);
    const hi = iv => (iv.set ? iv.set.utcMs : Infinity);

    function closestTo(list, mid) {
        let best = null;
        for (const e of list) if (best === null || Math.abs(e.utcMs - mid) < Math.abs(best.utcMs - mid)) best = e;
        return best;
    }

    /** The interval the table shows for this night, or null when none overlaps it. */
    function pickInterval(intervals, night) {
        const mid = (night.start + night.end) / 2;
        const overlapping = intervals.filter(iv => lo(iv) < night.end && hi(iv) > night.start);
        if (overlapping.length) {
            const holding = overlapping.find(iv => lo(iv) <= mid && mid <= hi(iv));
            if (holding) return holding;
            let best = null, bestSpan = -Infinity;
            for (const iv of overlapping) {
                const span = Math.min(hi(iv), night.end) - Math.max(lo(iv), night.start);
                if (span > bestSpan) { best = iv; bestSpan = span; }
            }
            return best;
        }
        return null;
    }

    /** The calendar-day offset of an instant's local date from the night's date. */
    function dayOffset(utcMs, date, tz, A) {
        const p = A.localParts(utcMs, tz);
        const d = parseDate(date);
        return Math.round((Date.UTC(p.year, p.month - 1, p.day) - Date.UTC(d.year, d.month - 1, d.day)) / DAY_MS);
    }

    /** 'HH:MM', 'HH:MM +1' or 'HH:MM −1' (U+2212); null stays null. */
    function timeLabel(hm, day) {
        if (hm === null || hm === undefined) return null;
        if (!day) return hm;
        return hm + ' ' + (day > 0 ? '+' + day : '−' + (-day));
    }

    // ------------------------------------------------------------------ rows

    function rows(opts) {
        const o = opts || {};
        const E = engine(), A = E.A, P = E.P, W = E.W;
        const s = site(o.inputs);

        const now = W.effectiveInstant(finite(o.nowMs, 'nowMs'), o.simDate, s.tz, A);
        const date = W.observingNight(now.wall);
        const night = nightWindow(date, s, A, W, null);
        const nightTimes = samples(night.start, night.end, s.interval);
        const mid = (night.start + night.end) / 2;
        const noon = localNoon(date, s.tz, A, W);
        const search = { start: noon - SEARCH_HOURS * HOUR_MS, end: noon + SEARCH_HOURS * HOUR_MS };

        const at = e => (e ? {
            hm: A.formatHM(e.utcMs, s.tz), utcMs: e.utcMs, day: dayOffset(e.utcMs, date, s.tz, A),
        } : { hm: null, utcMs: null, day: null });

        const out = P.BODIES.map(body => {
            const here = P.altAz(body, now.utcMs, s.lat, s.lon);
            const det = P.details(body, now.utcMs);
            const found = P.events(body, search.start, search.end, s.lat, s.lon);
            const intervals = upIntervals(found);
            const chosen = intervals.length ? pickInterval(intervals, night) : null;
            const allTransits = found.events.filter(e => e.kind === 'transit');
            const transitEvent = chosen ? closestTo(chosen.transits, mid) : closestTo(allTransits, mid);
            const rise = at(chosen ? chosen.rise : null);
            const set = at(chosen ? chosen.set : null);
            const transit = at(transitEvent);
            const maxAlt = maxAltitude(body, nightTimes, s, P);

            return {
                body: body,
                altNow: finite(here.alt, body + ' altitude'),
                azNow: finite(here.az, body + ' azimuth'),
                rise: rise.hm, riseUtc: rise.utcMs, riseDay: rise.day,
                transit: transit.hm, transitUtc: transit.utcMs, transitDay: transit.day,
                transitAlt: transitEvent ? transitEvent.alt : null,
                set: set.hm, setUtc: set.utcMs, setDay: set.day,
                alwaysUp: !intervals.length && !!found.alwaysUp,
                neverUp: !intervals.length && !!found.neverUp,
                notUpTonight: intervals.length > 0 && !chosen,
                maxAlt: maxAlt,
                muted: maxAlt < s.threshold,
                mag: det.mag,
                diameterArcsec: det.diameterArcsec,
                illuminatedPct: det.illuminated * 100,
                elongationDeg: Math.abs(det.elongationDeg),
                elongationSide: det.elongationDeg >= 0 ? 'E' : 'W',
            };
        });

        return {
            date: date,
            instantUtc: now.utcMs,
            threshold: s.threshold,
            night: night,
            searchWindow: search,
            rows: out,
        };
    }

    // ------------------------------------------------------------------ month series

    function nightlyMax(opts) {
        const o = opts || {};
        const E = engine(), A = E.A, P = E.P, W = E.W;
        const s = site(o.inputs);
        const body = String(o.body);
        if (P.BODIES.indexOf(body) < 0) throw new Error('nova_solar_rows: unknown body ' + JSON.stringify(o.body));
        const year = Math.trunc(+o.year), month = Math.trunc(+o.month);
        if (!(year >= 1 && year <= 9999)) throw new Error('nova_solar_rows: year out of range: ' + o.year);
        if (!(month >= 1 && month <= 12)) throw new Error('nova_solar_rows: month out of range: ' + o.month);

        const numDays = new Date(Date.UTC(year, month, 0)).getUTCDate();
        const sun = {};
        const dates = [], maxAlt = [];
        for (let day = 1; day <= numDays; day++) {
            const date = dateString(year, month, day);
            const night = nightWindow(date, s, A, W, sun);
            dates.push(date);
            maxAlt.push(maxAltitude(body, samples(night.start, night.end, s.interval), s, P));
        }
        return { dates: dates, max_alt: maxAlt };
    }

    // ------------------------------------------------------------------ selection

    function defaultBody(list) {
        let best = null;
        for (const r of list || []) if (best === null || r.maxAlt > best.maxAlt) best = r;
        return best ? best.body : null;
    }

    function pickBody(list, requested) {
        const r = (list || []).find(x => x.body === requested);
        return r ? r.body : defaultBody(list);
    }

    return {
        rows: rows, nightlyMax: nightlyMax, timeLabel: timeLabel,
        defaultBody: defaultBody, pickBody: pickBody,
    };
});
