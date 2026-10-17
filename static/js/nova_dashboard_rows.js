/*
 * nova_dashboard_rows.js — the desktop dashboard's object table, built in the browser.
 *
 * NovaDashboardRows.rows(opts) returns what nova/blueprints/api.py:get_desktop_data_batch()
 * returns for all of its pages together: the same rows in the same order, with the
 * same keys, types and strings, so the dashboard can fill its table without the
 * endpoint and fall back to it unchanged when anything here throws.
 *
 * Built on the two shared engines, looked up as globals at call time (the way
 * nova_plot_data.js finds them), so the page only has to load them first:
 *   NovaAstro   (static/js/nova_astro.js)    positions, sun events, the Moon
 *   NovaTransit (static/js/nova_transit.js)  the transit string
 *
 * opts:
 *   inputs    the /api/desktop_inputs response for the location: location,
 *             settings (calc_invisible, skyglow_flags), skyglow
 *   library   the /api/desktop_library objects: one row per enabled object, already
 *             in the batch's order, each to_dict() plus framing_rig, has_notes,
 *             session_count. Every row is copied and gets the astronomy keys.
 *   nowMs     the current instant, UTC milliseconds (Date.now())
 *   simDate   the simulation date 'YYYY-MM-DD', or null/'' for live
 *   instant   'snapped' (default) or 'exact', see below
 *
 * What is reproduced from the endpoint, including the parts that look odd:
 *   - The instant: live it is nowMs; with simDate it is simDate at the current wall
 *     clock of the location's zone, seconds and milliseconds included, read the way
 *     pytz's localize() reads a wall time (a skipped time with the offset before the
 *     change, a repeated time as the later, standard-time reading). An invalid
 *     simDate is ignored, as strptime's ValueError makes the endpoint ignore it.
 *   - The observing night is the instant's own wall date, or the day before when the
 *     wall hour is before 12, also in simulation. (NovaAstro.observingDate() steps
 *     back 12 absolute hours instead, which picks the other night for an hour on
 *     DST-change days, so it is not used.)
 *   - Altitude/Azimuth Current and Trend, with instant 'snapped', are those of the
 *     sample of the noon-to-noon grid (sampling_interval) nearest the instant, the
 *     earlier one on a tie; Trend compares it with the next sample ('–' at the last
 *     one, or when they differ by 0.01 deg or less). With instant 'exact' they are
 *     those of the instant itself and of the instant one interval later. Nothing
 *     else depends on the mode.
 *   - 11 PM is 23:00 local on the observing night, localized strictly (pytz
 *     is_dst=None): when 23:00 does not exist or exists twice the endpoint fails
 *     for the whole table, so this throws. Its obstruction test uses the unrounded
 *     position, its skyglow test the two-decimal strings, as the endpoint does.
 *   - The night for Observable Duration and Max Altitude (°) is
 *     calculate_observable_duration_vectorized()'s: dusk to dawn as 'HH:MM', with
 *     sunset/sunrise standing in when a twilight is missing (and no minutes at all
 *     when both are), localized like pytz, sampled every interval up to and including
 *     dawn. Max Altitude is the highest sample, mask or not.
 *   - The horizon mask is floored at the threshold before interpolating, and only
 *     used with more than one point. The skyglow floor is the nearest sector by
 *     absolute azimuth difference, without wrapping, first one on a tie, not floored
 *     at 0, and is only read when settings.skyglow_flags is true (the endpoint's
 *     NASA_EARTHDATA_TOKEN gate). Neither skyglow flag is set where the object is
 *     obstructed.
 *   - Geometric pre-filter: unless calc_invisible, an object whose culmination
 *     90 - |lat - dec| is below the threshold gets the endpoint's greyed-out row.
 *   - Numbers are printed as Python prints them: f"{x:.2f}", round(x, 1) and round(x)
 *     round the exact binary value, halves to even (JS toFixed() rounds halves up).
 *   - No error rows: a missing location, a non-finite coordinate or any engine
 *     failure throws, and the page asks the endpoint for the whole table instead.
 *
 * explain(opts) returns { rows, raw, night }: the same rows plus the unrounded values
 * each printed field was decided from. It is for tests/test_dashboard_rows_parity.py,
 * which allows a difference only where the browser's own value sits on a boundary.
 */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.NovaDashboardRows = factory();
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const MINUTE_MS = 60000;
    const HOUR_MS = 3600000;
    const OPPOSITION_MONTH = ['Oct', 'Nov', 'Dec', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep'];

    const pad2 = n => (n < 10 ? '0' : '') + n;

    function engine() {
        const A = typeof NovaAstro !== 'undefined' ? NovaAstro : null;
        const T = typeof NovaTransit !== 'undefined' ? NovaTransit : null;
        if (!A || !T) {
            throw new Error('nova_dashboard_rows: nova_astro.js and nova_transit.js must be loaded first');
        }
        return { A: A, T: T };
    }

    // ------------------------------------------------------------------ Python numbers

    /** True when x (>= 0) lies exactly halfway between two d-decimal numbers. */
    function isTie(x, d) {
        // A tie is j / 2^(d+1) with j odd; scaling by a power of two is exact.
        const t = x * Math.pow(2, d + 1);
        return Number.isInteger(t) && t % 2 === 1;
    }

    /** f"{x:.{d}f}" */
    function pyFixed(x, d) {
        if (!isFinite(x)) throw new Error('nova_dashboard_rows: cannot print ' + x);
        const neg = x < 0 || Object.is(x, -0);
        const a = Math.abs(x);
        let s;
        if (isTie(a, d)) {
            const lo = Math.floor(a * Math.pow(10, d));   // exact: a * 10^d is a half-integer
            const n = lo % 2 === 0 ? lo : lo + 1;
            const digits = String(n).padStart(d + 1, '0');
            s = d ? digits.slice(0, -d) + '.' + digits.slice(-d) : digits;
        } else {
            s = a.toFixed(d);
        }
        return (neg ? '-' : '') + s;
    }

    /** round(x, d) for d >= 1: the nearest double to the correctly rounded decimal. */
    function pyRound(x, d) {
        return Number(pyFixed(x, d));
    }

    /** round(x): an int, halves to even. */
    function pyRoundInt(x) {
        if (!isFinite(x)) throw new Error('nova_dashboard_rows: cannot round ' + x);
        const f = Math.floor(x), r = x - f;
        const n = r < 0.5 ? f : r > 0.5 ? f + 1 : (f % 2 === 0 ? f : f + 1);
        return n === 0 ? 0 : n;
    }

    /** Python truthiness of a JSON value. */
    function truthy(v) {
        if (v === null || v === undefined || v === false || v === 0 || v === '') return false;
        if (Array.isArray(v)) return v.length > 0;
        if (typeof v === 'object') return Object.keys(v).length > 0;
        return true;
    }

    // ------------------------------------------------------------------ pytz wall times

    function offsetAt(utcMs, tz, A) {
        const p = A.localParts(utcMs, tz);
        return Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second)
            - Math.floor(utcMs / 1000) * 1000;
    }

    /**
     * The UTC instants a wall time names (day overflow allowed): none when the
     * clocks skip it, two when they repeat it. The offsets in force a day and a
     * half either side are the only two the wall time can be read with.
     */
    function wallReadings(year, month, day, hour, minute, tz, A) {
        const wall = Date.UTC(year, month - 1, day, hour, minute, 0);
        const offsets = [offsetAt(wall - 36 * HOUR_MS, tz, A), offsetAt(wall + 36 * HOUR_MS, tz, A)];
        const found = [];
        for (const off of offsets) {
            const utc = wall - off;
            if (offsetAt(utc, tz, A) === off && found.indexOf(utc) < 0) found.push(utc);
        }
        return { wall: wall, offsets: offsets, found: found.sort((a, b) => a - b) };
    }

    /** pytz localize(): a repeated wall time is the later (standard) reading, a skipped
     *  one is read with the offset before the change. strict: is_dst=None, so throw. */
    function localize(year, month, day, hour, minute, tz, A, strict) {
        const r = wallReadings(year, month, day, hour, minute, tz, A);
        if (r.found.length === 1) return r.found[0];
        if (strict) {
            throw new Error('nova_dashboard_rows: ' + pad2(hour) + ':' + pad2(minute) + ' on '
                + year + '-' + pad2(month) + '-' + pad2(day) + ' is '
                + (r.found.length ? 'ambiguous' : 'non-existent') + ' in ' + tz);
        }
        return r.found.length ? r.found[1] : r.wall - Math.min(r.offsets[0], r.offsets[1]);
    }

    function parseDate(dateStr) {
        const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(dateStr));
        if (!m) throw new Error('nova_dashboard_rows: date must be YYYY-MM-DD, got ' + JSON.stringify(dateStr));
        return { year: +m[1], month: +m[2], day: +m[3] };
    }

    /** strptime(s, '%Y-%m-%d'), or null where it raises ValueError. */
    function parseSimDate(s) {
        const m = /^(\d{4})-(\d{1,2})-(\d{1,2})$/.exec(String(s));
        if (!m) return null;
        const year = +m[1], month = +m[2], day = +m[3];
        if (year < 1 || month < 1 || month > 12 || day < 1) return null;
        if (day > new Date(Date.UTC(year, month, 0)).getUTCDate()) return null;
        return { year: year, month: month, day: day };
    }

    function dateString(year, month, day) {
        const t = new Date(Date.UTC(year, month - 1, day));
        return String(t.getUTCFullYear()).padStart(4, '0') + '-' + pad2(t.getUTCMonth() + 1)
            + '-' + pad2(t.getUTCDate());
    }

    /** The batch's current_datetime_local, as a UTC instant plus its wall clock. */
    function effectiveInstant(nowMs, simDate, tz, A) {
        const p = A.localParts(nowMs, tz);
        const sim = simDate ? parseSimDate(simDate) : null;
        if (!sim) return { utcMs: nowMs, wall: p };
        const subMinute = p.second * 1000 + (((nowMs % 1000) + 1000) % 1000);
        return {
            utcMs: localize(sim.year, sim.month, sim.day, p.hour, p.minute, tz, A, false) + subMinute,
            wall: { year: sim.year, month: sim.month, day: sim.day, hour: p.hour, minute: p.minute },
        };
    }

    /** The observing night: the wall date, or the day before when the wall hour is < 12. */
    function observingNight(wall) {
        return dateString(wall.year, wall.month, wall.day - (wall.hour < 12 ? 1 : 0));
    }

    // ------------------------------------------------------------------ the night

    /** calculate_observable_duration_vectorized()'s sample times, or null for no darkness. */
    function nightWindow(dateStr, tz, interval, ev, A) {
        const d = parseDate(dateStr);
        const valid = s => !!s && s !== 'N/A';
        const at = (hm, plus) => localize(d.year, d.month, d.day + (plus || 0),
                                          +hm.slice(0, 2), +hm.slice(3, 5), tz, A, false);
        let duskStr = ev.astronomical_dusk, dawnStr = ev.astronomical_dawn, noAstroNight = false;
        if (!valid(duskStr) && !valid(dawnStr)) {
            if (!valid(ev.sunset) || !valid(ev.sunrise)) return null;
            noAstroNight = true;
            duskStr = ev.sunset;
            dawnStr = ev.sunrise;
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

        const step = interval * MINUTE_MS;
        const times = [];
        for (let t = dusk; t <= dawn; t += step) times.push(t);
        return {
            times: Float64Array.from(times), dusk: dusk, dawn: dawn, noAstroNight: noAstroNight,
            interval: interval, frame: A.frameFor(times[times.length >> 1]),
        };
    }

    /** _nearest_sg_alt(): smallest absolute azimuth difference, no wrap, first wins. */
    function nearestSkyglow(az, sectors) {
        let best = null, bestGap = Infinity, secondGap = Infinity;
        for (const s of sectors) {
            const gap = Math.abs(s.az_deg - az);
            if (gap < bestGap) { secondGap = bestGap; bestGap = gap; best = s; }
            else if (gap < secondGap) secondGap = gap;
        }
        return { floor: best ? best.min_alt_deg : null, tieMargin: secondGap - bestGap };
    }

    function finite(x, what) {
        const v = typeof x === 'number' ? x : NaN;
        if (!isFinite(v)) throw new Error('nova_dashboard_rows: ' + what + ' is not a finite number');
        return v;
    }

    // ------------------------------------------------------------------ rows

    function build(opts, withRaw) {
        const o = opts || {};
        const E = engine(), A = E.A, T = E.T;
        const inputs = o.inputs || {};
        const loc = inputs.location;
        if (!loc) throw new Error('nova_dashboard_rows: no location');
        if (!Array.isArray(o.library)) throw new Error('nova_dashboard_rows: library must be an array');
        const mode = o.instant === undefined || o.instant === null ? 'snapped' : o.instant;
        if (mode !== 'snapped' && mode !== 'exact') throw new Error('nova_dashboard_rows: unknown instant ' + mode);

        const lat = finite(loc.lat, 'lat'), lon = finite(loc.lon, 'lon'), tz = String(loc.timezone);
        const threshold = finite(loc.altitude_threshold, 'altitude_threshold');
        const interval = finite(loc.sampling_interval, 'sampling_interval');
        const mask = Array.isArray(loc.horizon_mask) && loc.horizon_mask.length > 1 ? loc.horizon_mask : null;
        const settings = inputs.settings || {};
        const calcInvisible = truthy(settings.calc_invisible);
        const sectors = settings.skyglow_flags === true && Array.isArray(inputs.skyglow) && inputs.skyglow.length
            ? inputs.skyglow : null;

        const now = effectiveInstant(finite(o.nowMs, 'nowMs'), o.simDate, tz, A);
        const date = observingNight(now.wall);
        const d = parseDate(date);

        // Once per table: the night, the grid, 11 PM, the frame for "now".
        const events = A.sunEvents(date, tz, lat, lon);
        const night = nightWindow(date, tz, interval, events, A);
        const grid = A.timeGrid(date, tz, interval);
        const eleven = localize(d.year, d.month, d.day, 23, 0, tz, A, true);
        const frameNow = A.frameFor(now.utcMs);
        const frameEleven = A.frameFor(eleven);

        let idx = 0;
        for (let i = 1; i < grid.length; i++) {
            if (Math.abs(grid[i] - now.utcMs) < Math.abs(grid[idx] - now.utcMs)) idx = i;
        }
        const nextIdx = Math.min(idx + 1, grid.length - 1);
        const curAt = mode === 'snapped' ? grid[idx] : now.utcMs;
        const nextAt = mode === 'snapped' ? grid[nextIdx] : now.utcMs + interval * MINUTE_MS;

        const required = az => A.interpolateHorizon(az, mask, threshold);
        const slope = az => mask ? Math.abs(required(az + 0.01) - required(az - 0.01)) / 0.02 : 0;

        const computed = [], computedIdx = [];
        const rows = new Array(o.library.length), raw = new Array(o.library.length);

        o.library.forEach((libRow, i) => {
            const ra = finite(libRow['RA (hours)'], 'RA of ' + libRow.Object);
            const dec = finite(libRow['DEC (degrees)'], 'DEC of ' + libRow.Object);
            const row = Object.assign({}, libRow);
            const bestMonth = OPPOSITION_MONTH[((Math.trunc(ra / 2) % 12) + 12) % 12];
            const culmination = 90.0 - Math.abs(lat - dec);
            rows[i] = row;

            if (!calcInvisible && culmination < threshold) {
                Object.assign(row, {
                    'Altitude Current': 'N/A', 'Azimuth Current': 'N/A', 'Trend': '–',
                    'Altitude 11PM': 'N/A', 'Azimuth 11PM': 'N/A', 'Transit Time': 'N/A',
                    'Observable Duration (min)': 0,
                    'Max Altitude (°)': pyRound(culmination, 1),
                    'Angular Separation (°)': 'N/A',
                    'is_obstructed_now': false,
                    'is_geometrically_impossible': true,
                    'best_month_ra': bestMonth,
                    'max_culmination_alt': pyRound(culmination, 1),
                    'error': false,
                });
                raw[i] = { prefilter: true };
                return;
            }

            const cur = A.altAz(ra, dec, lat, lon, curAt, frameNow);
            const next = A.altAz(ra, dec, lat, lon, nextAt, frameNow);
            const delta = next.alt - cur.alt;
            const trend = Math.abs(delta) > 0.01 ? (next.alt > cur.alt ? '↑' : '↓') : '–';

            const reqNow = mask ? required(cur.az) : null;
            const obstructedNow = mask !== null && cur.alt >= threshold && cur.alt < reqNow;
            const sgNow = sectors ? nearestSkyglow(cur.az, sectors) : null;
            const belowNow = !!sgNow && !obstructedNow && sgNow.floor !== null && sgNow.floor !== undefined
                && cur.alt < sgNow.floor;

            const e = A.altAz(ra, dec, lat, lon, eleven, frameEleven);
            const alt11 = pyFixed(e.alt, 2), az11 = pyFixed(e.az, 2);
            const req11 = mask ? required(e.az) : null;
            const obstructed11 = mask !== null && e.alt >= threshold && e.alt < req11;
            const sg11 = sectors ? nearestSkyglow(parseFloat(az11), sectors) : null;
            const below11 = !!sg11 && !obstructed11 && sg11.floor !== null && sg11.floor !== undefined
                && parseFloat(alt11) < sg11.floor;

            const dur = night
                ? A.observableDuration(ra, dec, lat, lon, date, tz,
                                       { threshold: threshold, interval: interval, mask: mask, window: night })
                : { minutes: 0, maxAltitude: 0 };
            const transit = T.transit(ra, dec, lat, lon, tz, date);

            Object.assign(row, {
                'Altitude Current': pyFixed(cur.alt, 2),
                'Azimuth Current': pyFixed(cur.az, 2),
                'Trend': trend,
                'Altitude 11PM': alt11,
                'Azimuth 11PM': az11,
                'Transit Time': transit.display,
                'Observable Duration (min)': Math.trunc(dur.minutes),
                'Max Altitude (°)': pyRound(dur.maxAltitude, 1),
                'Angular Separation (°)': null,   // filled below, one Moon for all rows
                'is_obstructed_now': obstructedNow,
                'below_skyglow_floor': belowNow,
                'below_skyglow_floor_11pm': below11,
                'is_obstructed_at_11pm': obstructed11,
                'best_month_ra': bestMonth,
                'max_culmination_alt': culmination,
                'error': false,
            });
            computed.push({ ra: ra, dec: dec });
            computedIdx.push(i);

            if (withRaw) {
                let near = 0;
                if (night) {
                    const curve = A.altAzCurve(ra, dec, lat, lon, night.times, night.frame);
                    for (let k = 0; k < curve.alt.length; k++) {
                        const min = mask ? required(curve.az[k]) : threshold;
                        near += Math.abs(curve.alt[k] - min) < 0.002 + slope(curve.az[k]) * 0.002 ? 1 : 0;
                    }
                }
                raw[i] = {
                    prefilter: false,
                    alt: cur.alt, az: cur.az, delta: delta,
                    reqNow: reqNow, slopeNow: slope(cur.az), sgNow: sgNow,
                    alt11: e.alt, az11: e.az, req11: req11, slope11: slope(e.az), sg11: sg11,
                    maxAltitude: dur.maxAltitude, nearSamples: near,
                    transit: transit.utcMsAll, transitCrossings: transit.crossings,
                };
            }
        });

        const seps = computed.length ? A.moonSeparations(computed, now.utcMs, lat, lon, frameNow) : [];
        computedIdx.forEach((rowIndex, k) => {
            rows[rowIndex]['Angular Separation (°)'] = pyRoundInt(seps[k]);
            if (withRaw) raw[rowIndex].sep = seps[k];
        });

        if (!withRaw) return rows;
        return {
            rows: rows, raw: raw,
            night: {
                date: date, instantMs: now.utcMs, gridIndex: idx, gridMs: grid[idx], elevenMs: eleven,
                sunEvents: events, window: night ? { dusk: night.dusk, dawn: night.dawn, samples: night.times.length } : null,
            },
        };
    }

    function rows(opts) {
        return build(opts, false);
    }

    // ------------------------------------------------------------------ status strip

    /**
     * statusStrip({inputs, nowMs, simDate}) returns the fields of
     * nova/blueprints/core.py:sun_events() that the dashboard's status strip shows:
     *   date   live: the wall date, or the day before when the wall hour is < 12;
     *          with simDate that date as given (no noon rule), or the wall date where
     *          strptime would reject it
     *   time   the location's wall clock 'HH:MM', in simulation too (localize() keeps it)
     *   phase  NovaAstro.moonPhase(date): 12:00 UTC, one decimal; ephem's
     *          calculate_moon_phase_cached() agrees to within 0.1
     *   astronomical_dusk, astronomical_dawn   calculate_sun_events() of that date
     * plus astronomical_dusk_utc / astronomical_dawn_utc (UTC ms, or null), for tests.
     * Throws on a missing location, a non-finite coordinate or any engine failure.
     */
    function statusStrip(opts) {
        const o = opts || {};
        const A = engine().A;
        const loc = (o.inputs || {}).location;
        if (!loc) throw new Error('nova_dashboard_rows: no location');
        const lat = finite(loc.lat, 'lat'), lon = finite(loc.lon, 'lon'), tz = String(loc.timezone);
        const wall = A.localParts(finite(o.nowMs, 'nowMs'), tz);
        let date;
        if (o.simDate) {
            const sim = parseSimDate(o.simDate);
            date = sim ? dateString(sim.year, sim.month, sim.day) : dateString(wall.year, wall.month, wall.day);
        } else {
            date = observingNight(wall);
        }
        const events = A.sunEvents(date, tz, lat, lon);
        return {
            date: date,
            time: pad2(wall.hour) + ':' + pad2(wall.minute),
            phase: finite(A.moonPhase(date), 'moon phase'),
            astronomical_dusk: events.astronomical_dusk,
            astronomical_dawn: events.astronomical_dawn,
            astronomical_dusk_utc: events.astronomical_dusk_utc,
            astronomical_dawn_utc: events.astronomical_dawn_utc,
        };
    }

    function explain(opts) {
        return build(opts, true);
    }

    return { rows: rows, explain: explain, statusStrip: statusStrip, _py: { fixed: pyFixed, round: pyRound, roundInt: pyRoundInt } };
});
