/*
 * nova_outlook.js — the Outlook table (good imaging nights), built in the browser.
 *
 * NovaOutlook.opportunities({inputs, nowMs, simDate}) returns what
 * nova/__init__.py:update_outlook_cache() puts in the cache file's
 * "opportunities": the same rows, with the same keys, types, strings and order,
 * so the Outlook table can be filled without the worker's cache and fall back to
 * it unchanged when anything here throws.
 *
 * Built on the two shared engines, looked up as globals at call time (the way
 * nova_plot_data.js finds them), so the page only has to load them first:
 *   NovaAstro          (static/js/nova_astro.js)          positions, sun events, the Moon
 *   NovaDashboardRows  (static/js/nova_dashboard_rows.js)  pytz localize(), the night
 *                      window and Python's rounding, reused rather than copied
 *
 * opts:
 *   inputs    the /api/desktop_inputs response for the location: location (lat,
 *             lon, timezone, altitude_threshold, sampling_interval, horizon_mask),
 *             settings (min_observable_minutes, max_moon_illumination,
 *             min_angular_separation, min_max_altitude, search_horizon_months) and
 *             objects, each with Object, ra, dec, ActiveProject, has_framing and
 *             the seven fields the row needs (common_name, project, type,
 *             constellation, magnitude, size, sb)
 *   nowMs     the current instant, UTC milliseconds (Date.now())
 *   simDate   the simulation date 'YYYY-MM-DD', or null/'' for live
 *
 * What is reproduced from the worker, including the parts that look odd:
 *   - Objects: the active projects only (active_project == true), regardless of
 *     `enabled`, in the payload's order — which is update_outlook_cache()'s live
 *     query order, /api/desktop_inputs's object order (AstroObject.id) and so the
 *     order of the whole result before it is sorted.
 *   - Nights: _outlook_start_date() as the sim date (a strptime '%Y-%m-%d' the
 *     module accepts — day "07" as well as "7", out of range rejected), else today
 *     at the location; then search_horizon_months * 30 consecutive calendar days,
 *     worked out on a plain calendar, not in the time zone.
 *   - Objects the payload cannot place (no finite ra/dec) are skipped, not fatal:
 *     get_ra_dec()'s guard returns no rows for such an object and the worker's
 *     per-object try keeps the run going, so the table simply lacks it. A missing
 *     location, a non-finite location coordinate or a settings block the engine
 *     cannot read does throw — the page then falls back to the worker's cache.
 *   - Per object and night, in the server's order: observable duration and the
 *     true maximum altitude over the night window (horizon mask floored at the
 *     threshold, sampled every interval up to and including dawn), then the two
 *     pre-filters (max altitude < min_max_altitude, or duration <
 *     min_observable_minutes minutes); the Moon's illumination at 00:00 local on
 *     the day after the night (NovaAstro.moonPhaseAt(), one decimal) against
 *     max_moon_illumination; the Moon's separation from the object at astronomical
 *     dusk — with the server's local 20:00 fallback where dusk is 'N/A' or
 *     unparseable — against min_angular_separation; then the composite score
 *     (0.20 altitude, 0.15 duration over 43200 s, 0.45 illumination, 0.20 dynamic
 *     separation), kept only when it is greater than 75. Stars are round(score/100
 *     * 4) + 1, Python's round, halves to even.
 *   - Once per night, shared by every object: the sun events, the Moon's
 *     illumination and the night window.
 *   - Printed as the server prints them: score is the composite float itself, not
 *     rounded (the worker stores float(composite_score)); max_alt and
 *     moon_illumination via round(x, 1) (halves to even — NovaDashboardRows._py);
 *     obs_dur is int(...), a truncation of the minutes. The catalogue strings (type, magnitude, size, sb)
 *     are passed through as they come; common_name and project stay null where the
 *     column is null.
 *
 * Known difference, by design: the server filters and scores on ephem's unrounded
 * illumination, this uses moonPhaseAt()'s one decimal. Measured through this code
 * path on the shipped catalogue (346 objects, 120 nights, the template's criteria):
 * London 0 rows appear or vanish (366 of 3755 rows differ in a field), Singapore 3
 * vanish and 8 appear — every flip in the score, none at the illumination filter.
 *
 * Pure: no DOM, no fetch, no timers. Any missing input, non-finite coordinate or
 * engine failure throws, and the page asks the worker's cache instead.
 */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.NovaOutlook = factory();
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const SCORING_WINDOW_SECONDS = 43200;   // nova/__init__.py:SCORING_WINDOW_SECONDS

    const pad2 = n => (n < 10 ? '0' : '') + n;

    function engine() {
        const A = typeof NovaAstro !== 'undefined' ? NovaAstro : null;
        const D = typeof NovaDashboardRows !== 'undefined' ? NovaDashboardRows : null;
        if (!A || !D) {
            throw new Error('nova_outlook: nova_astro.js and nova_dashboard_rows.js must be loaded first');
        }
        if (!D._wall || typeof D._wall.localize !== 'function' || typeof D._wall.nightWindow !== 'function'
                || !D._py || typeof D._py.round !== 'function' || typeof D._py.roundInt !== 'function') {
            throw new Error('nova_outlook: nova_dashboard_rows.js must export _wall and _py');
        }
        return { A: A, D: D, localize: D._wall.localize, nightWindow: D._wall.nightWindow };
    }

    function finite(x, what) {
        const v = typeof x === 'number' ? x : NaN;
        if (!isFinite(v)) throw new Error('nova_outlook: ' + what + ' is not a finite number');
        return v;
    }

    // ------------------------------------------------------------------ the start date

    /** strptime(s, '%Y-%m-%d').date(), or null where it raises ValueError. */
    function parseStartDate(s) {
        const m = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(String(s));
        if (!m) return null;
        const year = +m[1], month = +m[2], day = +m[3];
        if (year < 1 || month < 1 || month > 12 || day < 1) return null;
        if (day > new Date(Date.UTC(year, month, 0)).getUTCDate()) return null;
        return { year: year, month: month, day: day };
    }

    /**
     * _outlook_start_date(): the sim date when strptime accepts it, else today
     * at the location's zone (its wall date). An unknown zone makes the engine's
     * formatter throw, so the caller throws too.
     */
    function startDate(simDate, nowMs, tz, A) {
        const sim = simDate ? parseStartDate(simDate) : null;
        if (sim) return sim;
        const wall = A.localParts(nowMs, tz);
        return { year: wall.year, month: wall.month, day: wall.day };
    }

    /** 'YYYY-MM-DD' for a calendar day `offset` days on, day overflow allowed. */
    function dateStringOffset(start, offset) {
        const t = new Date(Date.UTC(start.year, start.month - 1, start.day + offset));
        return String(t.getUTCFullYear()).padStart(4, '0') + '-'
            + pad2(t.getUTCMonth() + 1) + '-' + pad2(t.getUTCDate());
    }

    // ------------------------------------------------------------------ one night

    /**
     * The three things the worker computes once per night and every object shares:
     * the sun events, the Moon's illumination at 00:00 local on the day after
     * `date`, and the night window. `duskMs` is the local 20:00 fallback the worker
     * uses when the astronomical dusk is 'N/A' or cannot be parsed.
     */
    function nightContext(date, tz, lat, lon, interval, localize, A, D) {
        const d = { year: +date.slice(0, 4), month: +date.slice(5, 7), day: +date.slice(8, 10) };
        const events = A.sunEvents(date, tz, lat, lon);
        const moonMs = localize(d.year, d.month, d.day + 1, 0, 0, tz, A, false);
        const phase = finite(A.moonPhaseAt(moonMs), 'moon phase');
        const window = D._wall.nightWindow(date, tz, interval, events, A);
        const duskStr = events.astronomical_dusk;
        const duskTime = /^\d{2}:\d{2}$/.test(duskStr) ? duskStr : '20:00';
        const duskMs = localize(d.year, d.month, d.day, +duskTime.slice(0, 2), +duskTime.slice(3, 5), tz, A, false);
        return { events: events, phase: phase, window: window, duskMs: duskMs };
    }

    // ------------------------------------------------------------------ the score

    function stars(compositeScore, roundInt) {
        return roundInt((compositeScore / 100) * 4) + 1;
    }

    function rating(stars) {
        return '★'.repeat(stars) + '☆'.repeat(5 - stars);
    }

    function score(maxAltitude, minutes, phase, separation) {
        const scoreAlt = Math.max(0, Math.min((maxAltitude - 20) / 70, 1));
        const scoreDuration = Math.min(minutes * 60 / SCORING_WINDOW_SECONDS, 1);
        const scoreMoonIllum = 1 - Math.min(phase / 100, 1);
        const scoreMoonSepDynamic = (1 - phase / 100) + (phase / 100) * Math.min(separation / 180, 1);
        return 100 * (0.20 * scoreAlt + 0.15 * scoreDuration + 0.45 * scoreMoonIllum + 0.20 * scoreMoonSepDynamic);
    }

    // ------------------------------------------------------------------ the rows

    function build(opts) {
        const o = opts || {};
        const E = engine(), A = E.A, D = E.D, localize = E.localize;
        const inputs = o.inputs || {};
        const loc = inputs.location;
        if (!loc) throw new Error('nova_outlook: no location');
        if (!Array.isArray(inputs.objects)) throw new Error('nova_outlook: inputs.objects must be an array');
        const settings = inputs.settings || {};

        const lat = finite(loc.lat, 'lat'), lon = finite(loc.lon, 'lon'), tz = String(loc.timezone);
        const threshold = finite(loc.altitude_threshold, 'altitude_threshold');
        const interval = finite(loc.sampling_interval, 'sampling_interval');
        const mask = Array.isArray(loc.horizon_mask) && loc.horizon_mask.length > 1 ? loc.horizon_mask : null;

        const minObservableMinutes = finite(settings.min_observable_minutes, 'min_observable_minutes');
        const maxMoonIllumination = finite(settings.max_moon_illumination, 'max_moon_illumination');
        const minAngularSeparation = finite(settings.min_angular_separation, 'min_angular_separation');
        const minMaxAltitude = finite(settings.min_max_altitude, 'min_max_altitude');
        const months = finite(settings.search_horizon_months, 'search_horizon_months');
        if (!(months >= 1)) throw new Error('nova_outlook: search_horizon_months must be at least 1');

        const nowMs = finite(o.nowMs, 'nowMs');
        const start = startDate(o.simDate, nowMs, tz, A);
        const nights = months * 30;

        // Active projects only, whatever `enabled` says, skipping any the payload
        // cannot place: get_ra_dec()'s guard returns no rows for such an object and
        // the worker's per-object try keeps going, so the table just lacks them.
        const projectObjects = inputs.objects.filter(obj => obj && obj.ActiveProject === true
            && typeof obj.ra === 'number' && isFinite(obj.ra)
            && typeof obj.dec === 'number' && isFinite(obj.dec));

        // One pass per night, the objects inside it, the way the server's outer loop
        // over dates and inner loop over active objects finish in the same order.
        const kept = [];
        for (let i = 0; i < nights; i++) {
            const date = dateStringOffset(start, i);
            const night = nightContext(date, tz, lat, lon, interval, localize, A, D);
            for (const obj of projectObjects) {
                const ra = obj.ra, dec = obj.dec;
                const dur = A.observableDuration(ra, dec, lat, lon, date, tz,
                    { threshold: threshold, interval: interval, mask: mask, window: night.window });
                if (dur.maxAltitude < minMaxAltitude || dur.minutes < minObservableMinutes) continue;

                const phase = night.phase;
                if (phase > maxMoonIllumination) continue;

                const separation = A.moonSeparation(ra, dec, night.duskMs, lat, lon);
                if (!isFinite(separation) || separation < minAngularSeparation) continue;

                const compositeScore = score(dur.maxAltitude, dur.minutes, phase, separation);
                if (!(compositeScore > 75)) continue;

                const rowStars = stars(compositeScore, D._py.roundInt);
                kept.push({
                    object_name: obj.Object,
                    common_name: obj.common_name === undefined ? null : obj.common_name,
                    has_framing: !!obj.has_framing,
                    date: date,
                    score: compositeScore,
                    rating: rating(rowStars),
                    rating_num: rowStars,
                    max_alt: D._py.round(dur.maxAltitude, 1),
                    obs_dur: Math.trunc(dur.minutes),
                    moon_illumination: D._py.round(phase, 1),
                    project: obj.project === undefined ? null : obj.project,
                    type: obj.type,
                    constellation: obj.constellation,
                    magnitude: obj.magnitude,
                    size: obj.size,
                    sb: obj.sb,
                });
            }
        }

        kept.sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
        return kept;
    }

    function opportunities(opts) {
        return build(opts);
    }

    return { opportunities: opportunities };
});
