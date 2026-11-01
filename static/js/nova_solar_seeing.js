/*
 * nova_solar_seeing.js — the Solar System page's seeing forecast from the jet stream.
 *
 * No DOM and no fetch: the page (static/js/solar_system.js) fetches /api/upper_wind
 * and renders what this returns. Nova only; not shared with nova-ios.
 *
 * The wind is /api/upper_wind's answer: { times: [ISO UTC, hourly], w200, w250, w300
 * (m/s, null where missing) }. Each hour's value is the highest of the three levels:
 * the jet core sits nearer 200 hPa in summer and the subtropics, nearer 300 hPa in
 * winter at high latitudes, so the maximum catches it wherever it is.
 *
 * The rules, per observing night:
 *   - The hours are the whole UTC hours inside the night window (the Bodies table's:
 *     sunset to sunrise, or local noon to noon without either; NovaSolarRows._night).
 *   - The body's best hours: those within ±SEEING_BEST_HOURS_HALF hours of its transit
 *     where it is at or above the altitude threshold; when that selects none, every
 *     hour it is at or above the threshold. A muted body (max altitude below the
 *     threshold) gets no rating.
 *   - The rating is the median of those hours (one gusty hour does not decide the
 *     night); the peak is their maximum. With fewer than SEEING_MIN_COVERAGE of the
 *     hours carrying a value there is no rating.
 *   - Median ≤ SEEING_STEADY_MAX_MS: steady; ≤ SEEING_AVERAGE_MAX_MS: average; above:
 *     poor. Tunable starting values (amateur rules of thumb), not published limits.
 *
 * rate(hours, wind) and bestHours(...) work on plain numbers; forecast(opts) runs them
 * over tonight and the next nights with the engines, looked up as globals at call time
 * like nova_solar_rows.js: NovaAstro, NovaPlanets, NovaDashboardRows, NovaSolarRows.
 *
 * forecast({inputs, body, date, wind, tonight}) returns
 *   { tonight: rating, outlook: [{ date, rating, reliable }] }
 * with a rating { level: 'steady'|'average'|'poor', median, max, hours, covered } or
 * { level: null, reason: 'muted'|'nohours'|'nodata' }. `tonight` optionally carries the
 * Bodies table's row ({transitUtc, muted}) so tonight agrees with the table. The outlook
 * is `date` and the next OUTLOOK_NIGHTS - 1 nights; a night without data ('nodata') is
 * left out, and from night RELIABLE_NIGHTS + 1 on `reliable` is false (beyond 3 days).
 */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.NovaSolarSeeing = factory();
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const SEEING_STEADY_MAX_MS = 15;
    const SEEING_AVERAGE_MAX_MS = 30;
    const SEEING_BEST_HOURS_HALF = 2;
    const SEEING_MIN_COVERAGE = 0.5;
    const OUTLOOK_NIGHTS = 5;
    const RELIABLE_NIGHTS = 3;
    const HOUR_MS = 3600000;
    const LEVELS = ['w200', 'w250', 'w300'];

    const isNum = v => typeof v === 'number' && isFinite(v);

    /** The wind as { utcMs: highest of the three levels, or null }. */
    function hourly(wind) {
        const out = {};
        const times = (wind && wind.times) || [];
        times.forEach((iso, i) => {
            const t = Date.parse(iso);
            if (!isFinite(t)) return;
            let best = null;
            LEVELS.forEach(k => {
                const v = ((wind[k] || [])[i]);
                if (isNum(v) && (best === null || v > best)) best = v;
            });
            out[t] = best;
        });
        return out;
    }

    function level(ms) {
        if (ms <= SEEING_STEADY_MAX_MS) return 'steady';
        if (ms <= SEEING_AVERAGE_MAX_MS) return 'average';
        return 'poor';
    }

    function median(values) {
        const v = values.slice().sort((a, b) => a - b);
        const mid = Math.floor(v.length / 2);
        return v.length % 2 ? v[mid] : (v[mid - 1] + v[mid]) / 2;
    }

    /** The whole UTC hours from start to end, both included when they fall on one. */
    function wholeHours(start, end) {
        const out = [];
        for (let t = Math.ceil(start / HOUR_MS) * HOUR_MS; t <= end; t += HOUR_MS) out.push(t);
        return out;
    }

    /**
     * The best hours of `hours` (UTC ms, with their altitudes `alts`): within
     * ±SEEING_BEST_HOURS_HALF of transitUtc and at or above threshold; else every hour at
     * or above threshold. transitUtc null: only the fallback.
     */
    function bestHours(hours, alts, transitUtc, threshold) {
        const up = hours.filter((t, i) => alts[i] >= threshold);
        if (isNum(transitUtc)) {
            const near = up.filter(t => Math.abs(t - transitUtc) <= SEEING_BEST_HOURS_HALF * HOUR_MS);
            if (near.length) return near;
        }
        return up;
    }

    /** The rating of these hours from hourly(wind). */
    function rate(hours, byHour) {
        if (!hours.length) return { level: null, reason: 'nohours' };
        const values = hours.map(t => byHour[t]).filter(isNum);
        if (values.length < SEEING_MIN_COVERAGE * hours.length) return { level: null, reason: 'nodata' };
        const m = median(values);
        return { level: level(m), median: m, max: Math.max.apply(null, values),
                 hours: hours.length, covered: values.length };
    }

    function engine() {
        const A = typeof NovaAstro !== 'undefined' ? NovaAstro : null;
        const P = typeof NovaPlanets !== 'undefined' ? NovaPlanets : null;
        const R = typeof NovaDashboardRows !== 'undefined' ? NovaDashboardRows : null;
        const S = typeof NovaSolarRows !== 'undefined' ? NovaSolarRows : null;
        if (!A || !P || !R || !S) {
            throw new Error('nova_solar_seeing: nova_astro.js, nova_planets.js, nova_dashboard_rows.js and nova_solar_rows.js must be loaded first');
        }
        return { A: A, P: P, W: R._wall, N: S._night };
    }

    /** The upper transit closest to the middle of the window, searched 12 h either side. */
    function transitNear(body, win, s, P) {
        const mid = (win.start + win.end) / 2;
        const found = P.events(body, win.start - 12 * HOUR_MS, win.end + 12 * HOUR_MS, s.lat, s.lon);
        let best = null;
        for (const e of found.events) {
            if (e.kind === 'transit' && (best === null || Math.abs(e.utcMs - mid) < Math.abs(best - mid))) best = e.utcMs;
        }
        return best;
    }

    function addDays(date, n) {
        const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(date));
        if (!m) throw new Error('nova_solar_seeing: date must be YYYY-MM-DD, got ' + JSON.stringify(date));
        const t = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3] + n));
        return t.toISOString().slice(0, 10);
    }

    function forecast(opts) {
        const o = opts || {};
        const E = engine(), P = E.P, N = E.N;
        const s = N.site(o.inputs);
        const body = String(o.body);
        const byHour = hourly(o.wind);
        const sun = {};

        function night(date, row) {
            const win = N.nightWindow(date, s, E.A, E.W, sun);
            const muted = row ? !!row.muted
                : N.maxAltitude(body, N.samples(win.start, win.end, s.interval), s, P) < s.threshold;
            if (muted) return { level: null, reason: 'muted' };
            const hours = wholeHours(win.start, win.end);
            const alts = hours.length ? P.altAzCurve(body, hours, s.lat, s.lon).alt : [];
            const transit = row && row.transitUtc !== undefined ? row.transitUtc : transitNear(body, win, s, P);
            return rate(bestHours(hours, alts, transit, s.threshold), byHour);
        }

        const tonight = night(o.date, o.tonight || null);
        const outlook = [];
        for (let i = 0; i < OUTLOOK_NIGHTS; i++) {
            const date = addDays(o.date, i);
            const rating = i === 0 ? tonight : night(date, null);
            if (rating.reason === 'nodata') continue;
            outlook.push({ date: date, rating: rating, reliable: i < RELIABLE_NIGHTS });
        }
        return { tonight: tonight, outlook: outlook };
    }

    return {
        SEEING_STEADY_MAX_MS: SEEING_STEADY_MAX_MS,
        SEEING_AVERAGE_MAX_MS: SEEING_AVERAGE_MAX_MS,
        SEEING_BEST_HOURS_HALF: SEEING_BEST_HOURS_HALF,
        SEEING_MIN_COVERAGE: SEEING_MIN_COVERAGE,
        OUTLOOK_NIGHTS: OUTLOOK_NIGHTS,
        RELIABLE_NIGHTS: RELIABLE_NIGHTS,
        hourly: hourly, level: level, median: median, wholeHours: wholeHours,
        bestHours: bestHours, rate: rate, forecast: forecast,
    };
});
