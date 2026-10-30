/*
 * nova_plot_data.js — the graph view's chart payloads, built in the browser.
 *
 * NovaPlotData.build(inputs) returns what nova/blueprints/api.py:get_plot_data()
 * returns for one object, one location and one observing night, and
 * NovaPlotData.range(inputs) what get_monthly_plot_data() returns for one object,
 * one location and one calendar month, so graph_view_chart.js can draw the day,
 * month and year charts without the endpoints and fall back to them unchanged
 * when anything here throws.
 *
 * Built on the two shared engines, looked up as globals at call time (the way
 * nova_transit.js finds NovaAstro), so the page only has to load them first:
 *   NovaAstro   (static/js/nova_astro.js)    positions, sun events, the time grid
 *   NovaTransit (static/js/nova_transit.js)  the transit string
 *
 * Inputs, from /api/desktop_inputs plus the page's own selection:
 *   raHours, decDeg   J2000 position ('ra'/'dec' of the object's row, in hours/deg)
 *   lat, lon, tz      the location block
 *   date              'YYYY-MM-DD', the observing night (noon to noon)
 *   threshold         location.altitude_threshold
 *   interval          location.sampling_interval, minutes
 *   mask              location.horizon_mask, [[az, alt], ...] (may be empty)
 *   skyglow           the skyglow block, [{az_deg, min_alt_deg}, ...], or null
 *
 * What is reproduced from the endpoint, including the parts that look odd:
 *   - 24 hours sampled from local noon with a sentinel at each end, so `times`
 *     has n + 2 entries and every series is null at both ends.
 *   - Every stamp is printed with local noon's UTC offset, the single offset
 *     pytz's localize() fixes for the window, so after a DST change the printed
 *     wall clocks stop matching the local clock. They name the same instants.
 *   - The horizon mask is floored at the threshold before interpolating; the
 *     skyglow floor is the nearest sector by absolute azimuth difference,
 *     without wrapping, floored at 0.
 *   - sun_events.current is the chosen night and .next the day after (whose five
 *     *_utc keys NovaAstro adds are dropped); transit_time is NovaTransit's
 *     display string; weather_forecast is always [] because the client fetches
 *     the weather itself.
 *
 * range() takes raHours, decDeg, lat, lon, tz as above plus year and month
 * (1-12, numbers or numeric strings, as the page's selects give them), and
 * reproduces get_monthly_plot_data():
 *   - one sample per calendar day at local midnight, the instant pytz's
 *     localize(datetime(y, m, d, 0, 0)) names: a midnight the clocks skip is read
 *     with the offset before the change, a midnight they repeat is the later,
 *     standard-time one. NovaAstro.localToUtc() gets the first right and not
 *     always the second, so localMidnight() settles it here.
 *   - `dates` are the requested calendar days as 'YYYY-MM-DD', whatever the
 *     offset; object_alt and moon_alt are unrounded and never null — the endpoint
 *     has no Moon fallback, so a Moon failure here throws, and the caller takes
 *     the endpoint's answer.
 *   - no sun events, transit, horizon mask or skyglow: the month and year charts
 *     draw only the two altitudes and a flat horizon line.
 *
 * A moving body (the Solar System page): both take an optional altAzAt(utcMs) ->
 * {alt, az}, the body's topocentric place at each sample, used instead of the fixed
 * raHours/decDeg (which are then not needed). A callback rather than a position per
 * sample, because NovaPlanets already adds the diurnal parallax a geocentric RA/Dec
 * would lose, up to a degree for the Moon. build() then has no transit_time (null):
 * NovaTransit's is a fixed star's. A non-finite alt or az from altAzAt throws.
 * Without altAzAt nothing here changes.
 */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.NovaPlotData = factory();
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const DAY_MS = 86400000;
    const HOUR_MS = 3600000;
    const SUN_KEYS = ['astronomical_dawn', 'sunrise', 'transit', 'sunset', 'astronomical_dusk'];

    const pad2 = n => (n < 10 ? '0' : '') + n;

    function engine() {
        const A = typeof NovaAstro !== 'undefined' ? NovaAstro : null;
        const T = typeof NovaTransit !== 'undefined' ? NovaTransit : null;
        if (!A || !T) {
            throw new Error('nova_plot_data: nova_astro.js and nova_transit.js must be loaded first');
        }
        return { A: A, T: T };
    }

    function parseDate(dateStr) {
        const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(dateStr));
        if (!m) throw new Error('nova_plot_data: date must be YYYY-MM-DD, got ' + JSON.stringify(dateStr));
        return { year: +m[1], month: +m[2], day: +m[3] };
    }

    /** 'YYYY-MM-DD' one calendar day later (the endpoint's local_date + 1 day). */
    function dayAfter(dateStr) {
        const d = parseDate(dateStr);
        const t = new Date(Date.UTC(d.year, d.month - 1, d.day + 1));
        return t.getUTCFullYear() + '-' + pad2(t.getUTCMonth() + 1) + '-' + pad2(t.getUTCDate());
    }

    /** The zone's UTC offset at an instant, the way NovaAstro.tzOffsetMs computes it. */
    function offsetAt(utcMs, tz, A) {
        const p = A.localParts(utcMs, tz);
        return Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second)
            - Math.floor(utcMs / 1000) * 1000;
    }

    /**
     * An instant as local ISO 8601 with an explicit offset, as Python's
     * datetime.isoformat() prints a pytz-localized datetime: seconds always
     * present, the offset's own seconds only when it has any.
     */
    function isoWithOffset(utcMs, offMs) {
        const d = new Date(utcMs + offMs);
        const a = Math.abs(offMs);
        let off = (offMs < 0 ? '-' : '+') + pad2(Math.floor(a / 3600000))
            + ':' + pad2(Math.floor(a % 3600000 / 60000));
        if (a % 60000) off += ':' + pad2(Math.floor(a % 60000 / 1000));
        return d.getUTCFullYear() + '-' + pad2(d.getUTCMonth() + 1) + '-' + pad2(d.getUTCDate())
            + 'T' + pad2(d.getUTCHours()) + ':' + pad2(d.getUTCMinutes()) + ':' + pad2(d.getUTCSeconds())
            + off;
    }

    /** calculate_sun_events(): the five keys the endpoint sends, without the _utc extras. */
    function sunBlock(dateStr, tz, lat, lon, A) {
        const ev = A.sunEvents(dateStr, tz, lat, lon);
        const out = {};
        for (const k of SUN_KEYS) out[k] = ev[k];
        return out;
    }

    /** _nearest_sg_alt(): smallest absolute azimuth difference, no wrap, first wins. */
    function nearestSkyglowAlt(az, sectors) {
        let best = null, bestGap = Infinity;
        for (const s of sectors) {
            const gap = Math.abs(s.az_deg - az);
            if (gap < bestGap) { bestGap = gap; best = s; }
        }
        return best ? best.min_alt_deg : null;
    }

    /** The skyglow floor series, sentinel-wrapped, or null when there is no floor. */
    function skyglowSeries(azimuths, sectors) {
        if (!sectors || !sectors.length) return null;
        const out = [null];
        for (const az of azimuths) {
            const v = nearestSkyglowAlt(az, sectors);
            out.push(v === null || v === undefined ? null : Math.max(0.0, v));
        }
        out.push(null);
        return out;
    }

    /** The horizon mask, floored at the threshold, or a flat threshold. */
    function horizonSeries(azimuths, mask, threshold, A) {
        const out = [null];
        if (mask && mask.length > 1) {
            for (const az of azimuths) out.push(A.interpolateHorizon(az, mask, threshold));
        } else {
            for (const az of azimuths) out.push(threshold);
        }
        out.push(null);
        return out;
    }

    function moonSeries(times, lat, lon, A) {
        const alt = [], az = [];
        for (const t of times) {
            const m = A.moonAltAz(t, lat, lon);
            alt.push(m.alt);
            az.push(m.az);
        }
        return { alt: alt, az: az };
    }

    /** The moving body's altAzAt(utcMs) -> {alt, az}, or null for a fixed position. */
    function trackOf(i) {
        if (i.altAzAt === undefined || i.altAzAt === null) return null;
        if (typeof i.altAzAt !== 'function') throw new Error('nova_plot_data: altAzAt must be a function');
        return i.altAzAt;
    }

    /** The moving body's place at utcMs, checked: a non-finite alt or az throws. */
    function trackAt(track, utcMs) {
        const p = track(utcMs) || {};
        const alt = +p.alt, az = +p.az;
        if (!isFinite(alt) || !isFinite(az)) {
            throw new Error('nova_plot_data: altAzAt returned a non-finite alt/az at ' + new Date(utcMs).toISOString());
        }
        return { alt: alt, az: az };
    }

    /** altAzCurve()'s shape for a moving body: its place at every sample. */
    function trackCurve(track, times) {
        const alt = new Float64Array(times.length), az = new Float64Array(times.length);
        for (let k = 0; k < times.length; k++) {
            const p = trackAt(track, times[k]);
            alt[k] = p.alt;
            az[k] = p.az;
        }
        return { alt: alt, az: az };
    }

    function build(inputs) {
        const i = inputs || {};
        const E = engine();
        const A = E.A;

        const date = String(i.date);
        parseDate(date);  // throws on anything that is not a plain date

        const lat = +i.lat, lon = +i.lon, tz = String(i.tz);
        const raHours = +i.raHours, decDeg = +i.decDeg;
        const track = trackOf(i);
        if (![lat, lon].concat(track ? [] : [raHours, decDeg]).every(isFinite)) {
            throw new Error('nova_plot_data: non-finite coordinates');
        }
        const threshold = i.threshold === undefined || i.threshold === null ? 20 : +i.threshold;

        // 24 hours from local noon, in absolute milliseconds, so a DST change inside
        // the window does not move a single sample. The sentinels are what the chart
        // draws its x range from.
        const grid = A.timeGrid(date, tz, i.interval);
        if (!grid.length) throw new Error('nova_plot_data: empty time grid');
        const startMs = grid[0], endMs = startMs + DAY_MS;
        const offMs = offsetAt(startMs, tz, A);
        const times = [isoWithOffset(startMs, offMs)];
        for (const t of grid) times.push(isoWithOffset(t, offMs));
        times.push(isoWithOffset(endMs, offMs));

        const curve = track ? trackCurve(track, grid) : A.altAzCurve(raHours, decDeg, lat, lon, grid);
        const azimuths = Array.from(curve.az);

        let moonAlt, moonAz;
        try {
            const m = moonSeries(grid, lat, lon, A);
            moonAlt = m.alt;
            moonAz = m.az;
        } catch (moonErr) {
            // The endpoint's own fallback: a null Moon series, not a failed chart.
            moonAlt = grid.map(() => null);
            moonAz = grid.map(() => null);
        }

        return {
            times: times,
            object_alt: [null].concat(Array.from(curve.alt), [null]),
            object_az: [null].concat(azimuths, [null]),
            moon_alt: [null].concat(moonAlt, [null]),
            moon_az: [null].concat(moonAz, [null]),
            horizon_mask_alt: horizonSeries(azimuths, i.mask, threshold, A),
            skyglow_alt: skyglowSeries(azimuths, i.skyglow),
            sun_events: {
                current: sunBlock(date, tz, lat, lon, A),
                next: sunBlock(dayAfter(date), tz, lat, lon, A),
            },
            transit_time: track ? null : E.T.transit(raHours, decDeg, lat, lon, tz, date).display,
            date: date,
            timezone: tz,
            weather_forecast: [],
        };
    }

    /**
     * The UTC instant of local midnight on a calendar day, as pytz's
     * localize(datetime(y, m, d, 0, 0)) (is_dst=False) resolves it. localToUtc()
     * already reads a skipped midnight with the earlier offset; when the wall time
     * exists twice, the offsets in force a day and a half either side give both
     * readings, and the later one is the standard-time reading pytz picks.
     */
    function localMidnight(year, month, day, tz, A) {
        let utc = A.localToUtc(year, month, day, 0, 0, tz);
        const wall = Date.UTC(year, month - 1, day, 0, 0, 0);
        for (const probe of [wall - 36 * HOUR_MS, wall + 36 * HOUR_MS]) {
            const off = offsetAt(probe, tz, A);
            const candidate = wall - off;
            if (candidate > utc && offsetAt(candidate, tz, A) === off) utc = candidate;
        }
        return utc;
    }

    function wholeNumber(value, lo, hi, what) {
        const s = String(value).trim();
        if (!/^[+-]?\d+$/.test(s)) throw new Error('nova_plot_data: ' + what + ' must be a whole number, got ' + JSON.stringify(value));
        const n = parseInt(s, 10);
        if (n < lo || n > hi) throw new Error('nova_plot_data: ' + what + ' out of range: ' + n);
        return n;
    }

    function range(inputs) {
        const i = inputs || {};
        const A = engine().A;

        // datetime() takes years 1..9999; the endpoint fails on anything else, so
        // this throws and the caller lets the endpoint say so.
        const year = wholeNumber(i.year, 1, 9999, 'year');
        const month = wholeNumber(i.month, 1, 12, 'month');
        const lat = +i.lat, lon = +i.lon, tz = String(i.tz);
        const raHours = +i.raHours, decDeg = +i.decDeg;
        const track = trackOf(i);
        if (![lat, lon].concat(track ? [] : [raHours, decDeg]).every(isFinite)) {
            throw new Error('nova_plot_data: non-finite coordinates');
        }

        const numDays = new Date(Date.UTC(year, month, 0)).getUTCDate();
        const dates = [], objectAlt = [], moonAlt = [];
        for (let day = 1; day <= numDays; day++) {
            const t = localMidnight(year, month, day, tz, A);
            // Each day its own frame: a month is long enough for aberration and
            // nutation to move, and the endpoint builds a fresh AltAz per day.
            objectAlt.push(track ? trackAt(track, t).alt : A.altAz(raHours, decDeg, lat, lon, t).alt);
            moonAlt.push(A.moonAltAz(t, lat, lon).alt);
            dates.push(String(year).padStart(4, '0') + '-' + pad2(month) + '-' + pad2(day));
        }
        return { dates: dates, object_alt: objectAlt, moon_alt: moonAlt };
    }

    return { build: build, range: range };
});
