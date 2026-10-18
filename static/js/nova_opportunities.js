/*
 * nova_opportunities.js — the graph view's Imaging Opportunities table, built in the browser.
 *
 * NovaOpportunities.forObject({inputs, objectName, nowMs}) returns what
 * core.get_imaging_opportunities() puts in its response's "results": the same
 * nights, with the same keys, types, strings and order, so the table can be filled
 * without the route and fall back to it unchanged when anything here throws.
 *
 * Built on three engines, looked up as globals at call time (the way
 * nova_outlook.js finds them), so the page only has to load them first:
 *   NovaAstro          (static/js/nova_astro.js)          positions, sun events, the Moon
 *   NovaDashboardRows  (static/js/nova_dashboard_rows.js)  pytz localize(), the night
 *                      window and Python's rounding
 *   NovaOutlook        (static/js/nova_outlook.js)         the per-night rule (_night):
 *                      start date, night context, score, stars — reused, not copied
 *
 * opts:
 *   inputs      the /api/desktop_inputs response for the page's location: location
 *               (lat, lon, timezone, altitude_threshold, sampling_interval,
 *               horizon_mask), settings (min_observable_minutes, min_max_altitude,
 *               max_moon_illumination, min_angular_separation, search_horizon_months)
 *               and objects, each with Object, ra and dec
 *   objectName  the page's object
 *   nowMs       the current instant, UTC milliseconds (Date.now())
 *
 * What is reproduced from the route, including the parts that look odd:
 *   - The object: the one row whose Object matches objectName ignoring case, as
 *     get_ra_dec() finds it in g.objects_map, whatever ActiveProject or enabled say.
 *   - Nights: today's calendar date at the location — not the observing night (at
 *     01:00 tonight's night is already gone) and never the sim date — then
 *     search_horizon_months * 30 consecutive calendar days.
 *   - Per night, in the route's order: observable duration and the true maximum
 *     altitude over the night window (horizon mask floored at the threshold, max_alt
 *     taken before the mask), dropped when duration < min_observable_minutes or
 *     max altitude < min_max_altitude; the Moon's illumination at 00:00 local on the
 *     day after the night (NovaAstro.moonPhaseAt(), one decimal), dropped when >
 *     max_moon_illumination; the Moon's separation from the object at astronomical
 *     dusk — local 20:00 where dusk is 'N/A' — dropped when < min_angular_separation.
 *     No score cut: every night that passes the four filters is kept, rated
 *     round(score / 100 * 4) + 1 stars with Python's round, halves to even.
 *   - from_time / to_time: the first and last observable sample, printed as the
 *     route's strftime('%H:%M') prints them: in the UTC offset in force at the start
 *     of the night window, carried through the night (pytz datetime + timedelta does
 *     not change offset). On a night with a DST change the time after the change is
 *     an hour off the wall clock, as on the server. 'N/A' when no sample is observable.
 *   - Printed as the route prints them: obs_minutes int(...), a truncation; max_alt
 *     and moon_illumination via Python's round(x, 1) (both are Python floats there);
 *     moon_separation via numpy's round(x, 1) — astropy's .deg is a numpy.float64,
 *     whose __round__ is rint(x * 10) / 10, so 0.15 gives 0.2 where Python gives 0.1.
 *
 * Known difference, by design (the same as Outlook's): the route filters and scores
 * on ephem's unrounded illumination, this uses moonPhaseAt()'s one decimal.
 *
 * Pure: no DOM, no fetch, no timers. A missing location, an object that is not in
 * inputs.objects exactly once (ignoring case) or has no finite ra/dec — the route
 * would ask SIMBAD — a missing criterion, an unexpected shape or any engine failure
 * throws, and the page asks the route instead.
 */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.NovaOpportunities = factory();
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const pad2 = n => (n < 10 ? '0' : '') + n;

    function engine() {
        const A = typeof NovaAstro !== 'undefined' ? NovaAstro : null;
        const D = typeof NovaDashboardRows !== 'undefined' ? NovaDashboardRows : null;
        const O = typeof NovaOutlook !== 'undefined' ? NovaOutlook : null;
        if (!A || !D || !O) {
            throw new Error('nova_opportunities: nova_astro.js, nova_dashboard_rows.js and nova_outlook.js must be loaded first');
        }
        if (!D._wall || typeof D._wall.localize !== 'function'
                || !D._py || typeof D._py.round !== 'function' || typeof D._py.roundInt !== 'function') {
            throw new Error('nova_opportunities: nova_dashboard_rows.js must export _wall and _py');
        }
        if (!O._night || typeof O._night.nightContext !== 'function') {
            throw new Error('nova_opportunities: nova_outlook.js must export _night');
        }
        return { A: A, D: D, N: O._night };
    }

    function finite(x, what) {
        const v = typeof x === 'number' ? x : NaN;
        if (!isFinite(v)) throw new Error('nova_opportunities: ' + what + ' is not a finite number');
        return v;
    }

    /** The one row get_ra_dec() would find: Object equal to `name` ignoring case. */
    function findObject(objects, name) {
        const wanted = String(name).toLowerCase();
        const matches = objects.filter(o => o && String(o.Object).toLowerCase() === wanted);
        if (matches.length !== 1) {
            throw new Error('nova_opportunities: ' + JSON.stringify(name) + ' matches '
                + matches.length + ' objects');
        }
        const obj = matches[0];
        return { ra: finite(obj.ra, 'ra'), dec: finite(obj.dec, 'dec') };
    }

    /** numpy.float64.__round__(1): rint(x * 10) / 10, halves to even on the scaled value. */
    function npRound1(x, roundInt) {
        return roundInt(x * 10) / 10;
    }

    /**
     * strftime('%H:%M') of a sample time as the server holds it: the night window's
     * first sample localized by pytz, plus k * interval, which keeps that first
     * sample's UTC offset all night. `startWall` is the first sample's wall time
     * read as if it were UTC.
     */
    function sampleHM(utcMs, window, startWall) {
        const t = new Date(startWall + (utcMs - window.times[0]));
        return pad2(t.getUTCHours()) + ':' + pad2(t.getUTCMinutes());
    }

    function forObject(opts) {
        const o = opts || {};
        const E = engine(), A = E.A, D = E.D, N = E.N;
        const inputs = o.inputs || {};
        const loc = inputs.location;
        if (!loc) throw new Error('nova_opportunities: no location');
        if (!Array.isArray(inputs.objects)) throw new Error('nova_opportunities: inputs.objects must be an array');
        const settings = inputs.settings;
        if (!settings || typeof settings !== 'object') throw new Error('nova_opportunities: no settings');

        const lat = finite(loc.lat, 'lat'), lon = finite(loc.lon, 'lon'), tz = String(loc.timezone);
        const threshold = finite(loc.altitude_threshold, 'altitude_threshold');
        const interval = finite(loc.sampling_interval, 'sampling_interval');
        if (loc.horizon_mask !== undefined && loc.horizon_mask !== null && !Array.isArray(loc.horizon_mask)) {
            throw new Error('nova_opportunities: horizon_mask must be an array');
        }
        const mask = Array.isArray(loc.horizon_mask) && loc.horizon_mask.length > 1 ? loc.horizon_mask : null;

        const minObservableMinutes = finite(settings.min_observable_minutes, 'min_observable_minutes');
        const minMaxAltitude = finite(settings.min_max_altitude, 'min_max_altitude');
        const maxMoonIllumination = finite(settings.max_moon_illumination, 'max_moon_illumination');
        const minAngularSeparation = finite(settings.min_angular_separation, 'min_angular_separation');
        const months = finite(settings.search_horizon_months, 'search_horizon_months');
        if (!(months >= 1)) throw new Error('nova_opportunities: search_horizon_months must be at least 1');

        const target = findObject(inputs.objects, o.objectName);
        const ra = target.ra, dec = target.dec;

        // Today at the location, never a sim date: the route's datetime.now(local_tz).date().
        const start = N.startDate(null, finite(o.nowMs, 'nowMs'), tz, A);

        const results = [];
        for (let i = 0; i < months * 30; i++) {
            const date = N.dateStringOffset(start, i);
            const night = N.nightContext(date, tz, lat, lon, interval, D._wall.localize, A, D);
            const dur = A.observableDuration(ra, dec, lat, lon, date, tz,
                { threshold: threshold, interval: interval, mask: mask, window: night.window });
            if (dur.minutes < minObservableMinutes || dur.maxAltitude < minMaxAltitude) continue;

            const phase = night.phase;
            if (phase > maxMoonIllumination) continue;

            const separation = finite(A.moonSeparation(ra, dec, night.duskMs, lat, lon), 'moon separation');
            if (separation < minAngularSeparation) continue;

            // 'N/A' when no sample is observable; from and to are null together.
            let fromTime = 'N/A', toTime = 'N/A';
            if (dur.from !== null) {
                // The window's first sample is its dusk: astronomical dusk, else sunset.
                const ev = night.events;
                const duskStr = ev.astronomical_dusk && ev.astronomical_dusk !== 'N/A' ? ev.astronomical_dusk : ev.sunset;
                if (!night.window || !/^\d{2}:\d{2}$/.test(duskStr)) {
                    throw new Error('nova_opportunities: an observable sample without a night window');
                }
                const startWall = Date.UTC(+date.slice(0, 4), +date.slice(5, 7) - 1, +date.slice(8, 10),
                    +duskStr.slice(0, 2), +duskStr.slice(3, 5));
                fromTime = sampleHM(dur.from, night.window, startWall);
                toTime = sampleHM(dur.to, night.window, startWall);
            }

            const rowStars = N.stars(N.score(dur.maxAltitude, dur.minutes, phase, separation), D._py.roundInt);
            results.push({
                date: date,
                obs_minutes: Math.trunc(dur.minutes),
                from_time: fromTime,
                to_time: toTime,
                max_alt: D._py.round(dur.maxAltitude, 1),
                moon_illumination: D._py.round(phase, 1),
                moon_separation: npRound1(separation, D._py.roundInt),
                rating: N.rating(rowStars),
            });
        }
        return results;
    }

    return { forObject: forObject };
});
