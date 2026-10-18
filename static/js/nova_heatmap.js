/*
 * nova_heatmap.js — the yearly heatmap (52 weeks x objects), built in the browser.
 *
 * NovaHeatmap.chunk(context, i) returns what nova/blueprints/api.py:
 * get_yearly_heatmap_chunk() returns for chunk_index=i: the same keys, in the same
 * order, with the same strings and the same rounding, so the heatmap can be drawn
 * without the server and fall back to it unchanged when anything here throws.
 *
 *   NovaHeatmap.prepare({inputs, library, nowMs})  the weeks, their Moon phases and
 *                                                   the objects, once per heatmap
 *   NovaHeatmap.chunk(context, i)                   one of the 12 chunks (0..11)
 *   NovaHeatmap.chunks({inputs, library, nowMs})    all 12, in order
 *
 * The split lets the page yield between chunks.
 *
 * Built on the two shared engines, looked up as globals at call time (the way
 * nova_outlook.js finds them), so the page only has to load them first:
 *   NovaAstro          (static/js/nova_astro.js)          positions, sun events, the Moon
 *   NovaDashboardRows  (static/js/nova_dashboard_rows.js)  pytz localize(), the night
 *                      window and Python's rounding, reused rather than copied
 *
 * opts:
 *   inputs    the /api/desktop_inputs response for the location: location (lat,
 *             lon, timezone, altitude_threshold, horizon_mask) and objects, every
 *             object of the user in id order, each with Object, ra, dec, enabled
 *             and ActiveProject
 *   library   the /api/desktop_library response: {objects: [...]}, one row per
 *             enabled object with the raw catalogue columns (Common Name, Type,
 *             Constellation, Magnitude, Size, SB), joined on Object
 *   nowMs     the current instant, UTC milliseconds (Date.now())
 *
 * What is reproduced from the route, including the parts that look odd:
 *   - Weeks: the Monday of the current week at the location (datetime.now(tz),
 *     minus its weekday()), worked out once — the route works it out per chunk —
 *     then 52 Mondays, chunks 0-3 holding 5 of them and chunks 4-11 holding 4.
 *     Each week is the night that starts on its Monday. Labels are '%b %d' in the
 *     C locale (English), dates '%Y-%m-%d'. The sim date plays no part.
 *   - The Moon: illumination at 00:00 local on the day after the Monday
 *     (NovaAstro.moonPhaseAt(), read with pytz's localize()), then round(x, 1).
 *   - Objects: the enabled rows with an RA and a Dec, in the payload's (id) order,
 *     kept when 90 - |lat - dec| >= the threshold, then a stable sort by RA.
 *   - The sampling interval is 60 minutes, whatever the user's setting.
 *   - Per object and week: observable duration and the true maximum altitude over
 *     the night window (horizon mask floored at the threshold, applied to the
 *     duration only); a score of 0 when max altitude < threshold or the duration
 *     < 45 minutes, else (0.4 * altitude + 0.6 * duration over 480 min) * 100, cut
 *     by up to 90 % above 60 % Moon; then round(score, 1). A week without a night
 *     window scores 0, as the route's (timedelta(0), 0) does.
 *   - Rows: y is common_name or object_name, plus ' [type]' when type is set;
 *     types and cons are str(x or ''); mags, sizes and sbs are Python's float() of
 *     the catalogue string (whitespace, underscores, Unicode digits, inf and nan
 *     included), else 999.0, 0.0 and 999.0. A nan or inf comes back as NaN or
 *     +-Infinity: the route puts the same value into its JSON as the bare token
 *     NaN / Infinity, which the browser's JSON parser rejects.
 *
 * Pure: no DOM, no fetch, no timers. A missing location, a non-finite coordinate,
 * an object the two payloads disagree on or an engine failure throws, and the page
 * asks the server instead.
 */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.NovaHeatmap = factory();
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const TOTAL_CHUNKS = 12;
    const TOTAL_WEEKS = 52;
    const SAMPLING_INTERVAL = 60;          // the route's sampling_interval = 60
    const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

    const pad2 = n => (n < 10 ? '0' : '') + n;

    function engine() {
        const A = typeof NovaAstro !== 'undefined' ? NovaAstro : null;
        const D = typeof NovaDashboardRows !== 'undefined' ? NovaDashboardRows : null;
        if (!A || !D) {
            throw new Error('nova_heatmap: nova_astro.js and nova_dashboard_rows.js must be loaded first');
        }
        if (!D._wall || typeof D._wall.localize !== 'function' || typeof D._wall.nightWindow !== 'function'
                || !D._py || typeof D._py.round !== 'function') {
            throw new Error('nova_heatmap: nova_dashboard_rows.js must export _wall and _py');
        }
        return { A: A, D: D };
    }

    function finite(x, what) {
        const v = typeof x === 'number' ? x : NaN;
        if (!isFinite(v)) throw new Error('nova_heatmap: ' + what + ' is not a finite number');
        return v;
    }

    // ------------------------------------------------------------------ Python's float()

    // str.isspace() outside ASCII; inside ASCII float() strips only ' \t\n\v\f\r'.
    const PY_SPACE = /^[\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]$/;
    const ASCII_SPACE = /^[ \t\n\v\f\r]+|[ \t\n\v\f\r]+$/g;
    const DIGITS = '[0-9](?:_?[0-9])*';
    const PY_DECIMAL = new RegExp('^[+-]?(?:' + DIGITS + '(?:\\.(?:' + DIGITS + ')?)?|\\.' + DIGITS + ')'
        + '(?:[eE][+-]?' + DIGITS + ')?$');
    const isNd = cp => /^\p{Nd}$/u.test(String.fromCodePoint(cp));

    /** A Unicode decimal digit's value: Nd digits come in runs of ten, 0 to 9. */
    function ndValue(cp) {
        let zero = cp;
        while (zero > 0 && isNd(zero - 1)) zero--;
        return (cp - zero) % 10;
    }

    /**
     * float(v), or null where it raises (the route's bare except). Non-ASCII
     * whitespace reads as a space and Unicode digits as their ASCII digit, as
     * CPython does before parsing; anything else outside ASCII is invalid.
     */
    function pyFloat(v) {
        if (typeof v === 'number') return v;
        if (typeof v === 'boolean') return v ? 1 : 0;
        if (typeof v !== 'string') return null;
        let s = '';
        for (const ch of v) {
            const cp = ch.codePointAt(0);
            if (cp < 128) s += ch;
            else if (PY_SPACE.test(ch)) s += ' ';
            else if (isNd(cp)) s += String(ndValue(cp));
            else return null;
        }
        s = s.replace(ASCII_SPACE, '');
        if (/^[+-]?(?:inf|infinity)$/i.test(s)) return s[0] === '-' ? -Infinity : Infinity;
        if (/^[+-]?nan$/i.test(s)) return NaN;
        if (!PY_DECIMAL.test(s)) return null;
        return Number(s.replace(/_/g, ''));
    }

    function floatOr(v, fallback) {
        const f = pyFloat(v);
        return f === null ? fallback : f;
    }

    /** Python truthiness of a catalogue column (a string or null). */
    const truthy = v => v !== null && v !== undefined && v !== '' && v !== false;

    // ------------------------------------------------------------------ the weeks

    function dateParts(year, month, day) {
        const t = new Date(Date.UTC(year, month - 1, day));
        return { year: t.getUTCFullYear(), month: t.getUTCMonth() + 1, day: t.getUTCDate() };
    }

    function dateString(p) {
        return String(p.year).padStart(4, '0') + '-' + pad2(p.month) + '-' + pad2(p.day);
    }

    /** now.date() - timedelta(days=now.weekday()) with now = datetime.now(tz). */
    function weekStart(nowMs, tz, A) {
        const wall = A.localParts(nowMs, tz);
        const weekday = (new Date(Date.UTC(wall.year, wall.month - 1, wall.day)).getUTCDay() + 6) % 7;
        return dateParts(wall.year, wall.month, wall.day - weekday);
    }

    /** The route's week range for chunk i: start_week up to (not including) end_week. */
    function chunkWeeks(i) {
        const perChunk = Math.floor(TOTAL_WEEKS / TOTAL_CHUNKS);
        const remainder = TOTAL_WEEKS % TOTAL_CHUNKS;
        const start = i * perChunk + Math.min(i, remainder);
        return { start: start, end: start + perChunk + (i < remainder ? 1 : 0) };
    }

    // ------------------------------------------------------------------ the inputs

    function readMask(mask) {
        if (mask === undefined || mask === null) return null;
        if (!Array.isArray(mask)) throw new Error('nova_heatmap: horizon_mask must be an array');
        for (const p of mask) {
            if (!Array.isArray(p) || p.length !== 2) throw new Error('nova_heatmap: horizon_mask point must be [az, alt]');
            finite(p[0], 'horizon_mask azimuth');
            finite(p[1], 'horizon_mask altitude');
        }
        return mask.length > 1 ? mask : null;
    }

    /** The enabled objects in the payload's order, each joined to its library row. */
    function readObjects(inputs, library) {
        if (!Array.isArray(inputs.objects)) throw new Error('nova_heatmap: inputs.objects must be an array');
        if (!library || !Array.isArray(library.objects)) throw new Error('nova_heatmap: library.objects must be an array');

        const rows = new Map();
        for (const row of library.objects) {
            if (!row || typeof row.Object !== 'string') throw new Error('nova_heatmap: library row without Object');
            if (rows.has(row.Object)) throw new Error('nova_heatmap: library lists ' + row.Object + ' twice');
            rows.set(row.Object, row);
        }

        const enabled = [];
        for (const obj of inputs.objects) {
            if (!obj || typeof obj.Object !== 'string') throw new Error('nova_heatmap: inputs row without Object');
            if (typeof obj.enabled !== 'boolean') throw new Error('nova_heatmap: inputs row without enabled');
            if (!obj.enabled) continue;
            const row = rows.get(obj.Object);
            if (!row) throw new Error('nova_heatmap: ' + obj.Object + ' is enabled but not in the library');
            rows.delete(obj.Object);
            enabled.push({ obj: obj, row: row });
        }
        if (rows.size) throw new Error('nova_heatmap: the library has objects the inputs do not enable');
        return enabled;
    }

    // ------------------------------------------------------------------ prepare

    function prepare(opts) {
        const o = opts || {};
        const E = engine(), A = E.A, D = E.D;
        const inputs = o.inputs || {};
        const loc = inputs.location;
        if (!loc) throw new Error('nova_heatmap: no location');

        const lat = finite(loc.lat, 'lat'), lon = finite(loc.lon, 'lon');
        if (typeof loc.timezone !== 'string' || !loc.timezone) throw new Error('nova_heatmap: no timezone');
        const tz = loc.timezone;
        const threshold = finite(loc.altitude_threshold, 'altitude_threshold');
        const mask = readMask(loc.horizon_mask);
        const nowMs = finite(o.nowMs, 'nowMs');

        // The objects: enabled, placed, geometrically visible, stable-sorted by RA
        const objects = [];
        for (const pair of readObjects(inputs, o.library)) {
            const obj = pair.obj, row = pair.row;
            if (obj.ra === null || obj.ra === undefined || obj.dec === null || obj.dec === undefined) continue;
            const ra = finite(obj.ra, 'ra of ' + obj.Object), dec = finite(obj.dec, 'dec of ' + obj.Object);
            if (!(90 - Math.abs(lat - dec) >= threshold)) continue;

            let name = truthy(row['Common Name']) ? String(row['Common Name']) : obj.Object;
            if (truthy(row.Type)) name += ' [' + row.Type + ']';
            objects.push({
                ra: ra, dec: dec, y: name, id: obj.Object,
                active: obj.ActiveProject ? 1 : 0,
                type: truthy(row.Type) ? String(row.Type) : '',
                con: truthy(row.Constellation) ? String(row.Constellation) : '',
                mag: floatOr(row.Magnitude, 999.0),
                size: floatOr(row.Size, 0.0),
                sb: floatOr(row.SB, 999.0),
            });
        }
        objects.sort((a, b) => a.ra - b.ra);

        // The weeks: 52 Mondays from this week's, each with its Moon
        const start = weekStart(nowMs, tz, A);
        const weeks = [];
        for (let i = 0; i < TOTAL_WEEKS; i++) {
            const d = dateParts(start.year, start.month, start.day + 7 * i);
            const moonMs = D._wall.localize(d.year, d.month, d.day + 1, 0, 0, tz, A, false);
            weeks.push({
                date: dateString(d),
                x: MONTHS[d.month - 1] + ' ' + pad2(d.day),
                phase: D._py.round(finite(A.moonPhaseAt(moonMs), 'moon phase'), 1),
            });
        }

        return { lat: lat, lon: lon, tz: tz, threshold: threshold, mask: mask, weeks: weeks, objects: objects };
    }

    // ------------------------------------------------------------------ one chunk

    function score(maxAlt, minutes, phase, threshold) {
        if (maxAlt < threshold || minutes < 45) return 0;
        if (threshold === 90) throw new Error('nova_heatmap: altitude_threshold 90 divides by zero');
        const normAlt = Math.min((maxAlt - threshold) / (90 - threshold), 1.0);
        const normDur = Math.min(minutes / 480, 1.0);
        let s = (0.4 * normAlt + 0.6 * normDur) * 100;
        if (phase > 60) {
            const penaltyFactor = ((phase - 60) / 40) * 0.9;
            s *= (1 - penaltyFactor);
        }
        return s;
    }

    function chunk(context, i) {
        const c = context;
        if (!c || !Array.isArray(c.weeks) || !Array.isArray(c.objects)) throw new Error('nova_heatmap: not a prepared context');
        if (!(Number.isInteger(i) && i >= 0 && i < TOTAL_CHUNKS)) throw new Error('nova_heatmap: chunk index must be 0..11');
        const E = engine(), A = E.A, D = E.D;
        const range = chunkWeeks(i);
        const weeks = c.weeks.slice(range.start, range.end);

        const z = c.objects.map(() => []);
        for (const week of weeks) {
            const events = A.sunEvents(week.date, c.tz, c.lat, c.lon);
            const window = D._wall.nightWindow(week.date, c.tz, SAMPLING_INTERVAL, events, A);
            c.objects.forEach((obj, k) => {
                let maxAlt = 0, minutes = 0;
                if (window) {
                    const dur = A.observableDuration(obj.ra, obj.dec, c.lat, c.lon, week.date, c.tz,
                        { threshold: c.threshold, interval: SAMPLING_INTERVAL, mask: c.mask, window: window });
                    maxAlt = dur.maxAltitude;
                    minutes = dur.minutes;
                }
                z[k].push(D._py.round(score(maxAlt, minutes, week.phase, c.threshold), 1));
            });
        }

        return {
            chunk_index: i,
            x: weeks.map(w => w.x),
            z_chunk: z,
            y: c.objects.map(o => o.y),
            moon_phases: weeks.map(w => w.phase),
            ids: c.objects.map(o => o.id),
            active: c.objects.map(o => o.active),
            dates: weeks.map(w => w.date),
            types: c.objects.map(o => o.type),
            cons: c.objects.map(o => o.con),
            mags: c.objects.map(o => o.mag),
            sizes: c.objects.map(o => o.size),
            sbs: c.objects.map(o => o.sb),
        };
    }

    function chunks(opts) {
        const context = prepare(opts);
        const out = [];
        for (let i = 0; i < TOTAL_CHUNKS; i++) out.push(chunk(context, i));
        return out;
    }

    return { prepare: prepare, chunk: chunk, chunks: chunks, TOTAL_CHUNKS: TOTAL_CHUNKS, _pyFloat: pyFloat };
});
