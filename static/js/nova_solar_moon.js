/*
 * nova_solar_moon.js — the Solar System page's Moon card and lunar Calendar events,
 * built in the browser.
 *
 * The rules: which named features are near the terminator tonight, which limb the
 * libration turns towards us, the next good nights for a feature, the Lunar X and V and
 * the favourable libration nights. No DOM: the page (static/js/solar_system.js) draws.
 *
 * Built on the shared engines and the Bodies tab's own rules, looked up as globals at
 * call time, so the page only has to load them first:
 *   NovaAstro          (static/js/nova_astro.js)           Sun, Moon, time zones
 *   NovaPlanets        (static/js/nova_planets.js)         the Moon's altitude and details
 *   NovaMoon           (static/js/nova_moon.js)            libration, colongitude, projection
 *   NovaMoonFeatures   (static/js/nova_moon_features.js)   the curated features
 *   NovaDashboardRows  (static/js/nova_dashboard_rows.js)  the instant and the night
 *   NovaSolarRows      (static/js/nova_solar_rows.js)      night window and its samples
 *
 * opts (every function): inputs (the /api/desktop_inputs response; .location's lat, lon,
 * timezone, altitude_threshold, sampling_interval), nowMs (Date.now()), simDate
 * ('YYYY-MM-DD' or null/'' for live).
 *
 * tonight(opts) -> the Moon card for the current observing night:
 *   - The night and its window are the Bodies tab's (sunset to sunrise, or noon to noon
 *     without either), sampled every sampling_interval minutes. "Tonight" is the sample
 *     where the Moon is highest (instantUtc, hm), live and in simulation alike; when it
 *     never rises in the window, the window's midpoint. maxAlt is that altitude. muted:
 *     maxAlt below altitude_threshold; mutedReason 'notUp' (below the horizon all night)
 *     or 'altitude', else null.
 *   - geometry: NovaMoon.geometry() at the instant; illuminated (0..1) and the rest of
 *     the Moon's NovaPlanets.details().
 *   - Terminator (features): a feature of NovaMoonFeatures is listed when it is on the
 *     side facing us (fromCentre below VISIBLE_DEG) and the Sun's altitude h over its
 *     centre satisfies -r <= h <= hMax + r, r its angular radius ((D / 2) / 1737.4 km in
 *     degrees, so a terminator that crosses it counts; at most RADIUS_CAP_DEG, or the
 *     largest plains would count at any phase) and hMax BAND_DEG, or
 *     BAND_WIDE_DEG for the broad plains (mare, oceanus, sinus, lacus). morning: the Sun
 *     rising there (NovaMoon.isMorning). nearLimb: fromCentre above NEAR_LIMB_DEG.
 *     Ranked by tier, then how close h is to IDEAL_SUN_DEG, then diameter (larger
 *     first). top: the first TOP_COUNT, more: the rest. None while the Moon is nearer
 *     new than NEW_MOON_FRACTION illuminated.
 *   - empty: null when features has entries, else 'new', 'full' (illuminated above
 *     FULL_MOON_FRACTION) or 'none'.
 *   - libration (hint()): see below.
 *
 * hint(g) -> the libration line for geometry g:
 *   { amount, direction (NovaMoon.tilt()), level: 'none' below 3 deg, 'moderate' below
 *     5, 'favourable' below 7, else 'excellent' (LEVELS), side (the name of the feature
 *     the side is called by), also (up to two more names), litLimb, display }.
 *   The names are limb features (limb: true) on the side facing us (fromCentre below
 *     90), within SECTOR_DEG of the tilt's direction (their own direction from the mean
 *     centre), by tier then diameter; with none in the sector, the visible limb feature
 *     nearest in direction. litLimb: the Sun is up over the centre of the feature the
 *     side is called by. also lists only lit features, and none while the side is dark.
 *   display: what the page says: level, except 'dark' when the side is turned towards us
 *     (level not 'none') but unlit; a tilt towards a dark limb is never favourable.
 *
 * nextNights(opts) -> { nights: [{ date, utcMs }], byName: { name: ['YYYY-MM-DD', ...] } }
 *   for the NEXT_NIGHTS nights after tonight: each night's Moon at its highest sample
 *   (as tonight()), kept when that is at or above altitude_threshold; a feature's dates
 *   are the kept nights where it passes the terminator rule, at most NEXT_LIMIT.
 *
 * calendarRows(opts) -> { window, rows } in the Calendar's window (local noon 30 days
 *   before the night's date to local noon 24 months after it) and row shape
 *   (nova_solar_calendar.js), body 'moon':
 *   - 'lunarX': colongitude LUNAR_X_COLONGITUDE (David Chapman's mean for the Lunar X,
 *     358.0 +/- 1.2 deg; the Lunar V near Ukert forms at about the same time), found
 *     every 12 h and bisected to a minute. Muted, mutedReason 'lunarWindow', unless
 *     within LUNAR_X_WINDOW_MS of it some sample has the Sun down (below
 *     NovaAstro.SUN_RISE_SET_ALT) and the Moon at or above altitude_threshold.
 *   - 'libration': the maxima of NovaMoon.tilt().amount at or above
 *     LIBRATION_EVENT_DEG, sampled every 6 h, golden section to a minute, kept only
 *     when the favoured limb is lit (hint().litLimb, so display is 'excellent'), the
 *     same rule as the Moon card's line. `libration` carries the hint.
 *     Muted, mutedReason 'altitude', when the Moon's highest altitude that night is
 *     below altitude_threshold.
 *   Everything else as the Calendar's rows: night, hm, hmDay, daysFromNow, mag,
 *   diameterArcsec, elongationDeg (the Moon's, unsigned), maxAlt (that night's).
 *
 * diagram(g, mode, features) -> the Moon drawing on a unit disc in SVG coordinates
 *   (y down), north up in 'normal'; 'diagonal' mirrors left and right (a star diagonal),
 *   'inverted' turns it 180 deg (a Newtonian, or a refractor without a diagonal):
 *     lit         SVG path of the lit part (null near new Moon), terminator its edge
 *     markers     [{ x, y, n }] for `features` (1-based n), only those facing us
 *     arrow       { x, y } unit vector towards the limb turned towards us
 *     north       { x, y } where lunar north is
 *
 * Everything throws on a missing location, a non-finite coordinate or any engine
 * failure; the page shows its error instead.
 */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.NovaSolarMoon = factory();
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const MINUTE_MS = 60000;
    const HOUR_MS = 3600000;
    const DAY_MS = 86400000;
    const DEG = Math.PI / 180;
    const MOON_RADIUS_KM = 1737.4;

    const VISIBLE_DEG = 85;
    const NEAR_LIMB_DEG = 75;
    const BAND_DEG = 10;
    const RADIUS_CAP_DEG = 5;
    const BAND_WIDE_DEG = 15;
    const WIDE_TYPES = Object.freeze(['mare', 'oceanus', 'sinus', 'lacus']);
    const IDEAL_SUN_DEG = 4;
    const TOP_COUNT = 8;
    const NEW_MOON_FRACTION = 0.05;
    const FULL_MOON_FRACTION = 0.9;
    const LEVELS = Object.freeze([[7, 'excellent'], [5, 'favourable'], [3, 'moderate']]);
    const SECTOR_DEG = 45;
    const NEXT_NIGHTS = 30;
    const NEXT_LIMIT = 3;
    const LUNAR_X_COLONGITUDE = 358;
    const LUNAR_X_WINDOW_MS = 2 * HOUR_MS;
    const LIBRATION_EVENT_DEG = 7;
    const DAYS_BACK = 30;
    const MONTHS_AHEAD = 24;
    const GOLDEN = 0.618034;
    const VIEWS = Object.freeze(['normal', 'diagonal', 'inverted']);

    function engine() {
        const A = typeof NovaAstro !== 'undefined' ? NovaAstro : null;
        const P = typeof NovaPlanets !== 'undefined' ? NovaPlanets : null;
        const M = typeof NovaMoon !== 'undefined' ? NovaMoon : null;
        const F = typeof NovaMoonFeatures !== 'undefined' ? NovaMoonFeatures : null;
        const R = typeof NovaDashboardRows !== 'undefined' ? NovaDashboardRows : null;
        const S = typeof NovaSolarRows !== 'undefined' ? NovaSolarRows : null;
        if (!A || !P || !M || !F || !R || !S) {
            throw new Error('nova_solar_moon: nova_astro.js, nova_planets.js, nova_moon.js, nova_moon_features.js, '
                + 'nova_dashboard_rows.js and nova_solar_rows.js must be loaded first');
        }
        return { A: A, P: P, M: M, F: F.FEATURES, W: R._wall, N: S._night };
    }

    function finite(x, what) {
        const v = typeof x === 'number' ? x : NaN;
        if (!isFinite(v)) throw new Error('nova_solar_moon: ' + what + ' is not a finite number');
        return v;
    }

    function parseDate(dateStr) {
        const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(dateStr));
        if (!m) throw new Error('nova_solar_moon: date must be YYYY-MM-DD, got ' + JSON.stringify(dateStr));
        return { year: +m[1], month: +m[2], day: +m[3] };
    }

    const dayNumber = d => Date.UTC(d.year, d.month - 1, d.day) / DAY_MS;
    const mod = (a, n) => ((a % n) + n) % n;
    const angleDiff = (a, b) => Math.abs(mod(a - b + 180, 360) - 180);

    /** The Sun's altitude in degrees, as nova_solar_calendar.js computes it. */
    function sunAltDeg(utcMs, lat, lon, A) {
        const p = A.sunPosition(utcMs);
        const ha = A.gast(utcMs, p.nut) + lon * DEG - p.ra;
        const sinAlt = Math.sin(lat * DEG) * Math.sin(p.dec) + Math.cos(lat * DEG) * Math.cos(p.dec) * Math.cos(ha);
        return Math.asin(Math.max(-1, Math.min(1, sinAlt))) / DEG;
    }

    /** The current instant, the observing night and the site, as the Bodies tab has them. */
    function context(opts) {
        const o = opts || {};
        const E = engine();
        const s = E.N.site(o.inputs);
        const now = E.W.effectiveInstant(finite(o.nowMs, 'nowMs'), o.simDate, s.tz, E.A);
        return { E: E, s: s, now: now, date: E.W.observingNight(now.wall) };
    }

    /** The night's Moon: its highest sample in the night window (the midpoint when it is
     *  below the horizon all night). */
    function nightMoon(date, c, sun) {
        const E = c.E, s = c.s;
        const win = E.N.nightWindow(date, s, E.A, E.W, sun);
        const times = E.N.samples(win.start, win.end, s.interval);
        const curve = E.P.altAzCurve('moon', times, s.lat, s.lon);
        let k = 0;
        for (let i = 1; i < curve.alt.length; i++) if (curve.alt[i] > curve.alt[k]) k = i;
        const maxAlt = finite(curve.alt[k], 'moon altitude');
        const utcMs = maxAlt >= 0 ? times[k] : Math.round((win.start + win.end) / 2);
        return { window: win, utcMs: utcMs, maxAlt: maxAlt };
    }

    // ------------------------------------------------------------------ terminator

    /** The terminator rule for one feature, or null when it is not near the terminator. */
    function nearTerminator(f, g, M) {
        const fromCentre = M.fromCentre(g, f.lat, f.lon);
        if (!(fromCentre < VISIBLE_DEG)) return null;
        const h = M.sunAltitude(g, f.lat, f.lon);
        const r = Math.min(RADIUS_CAP_DEG, f.diameterKm / 2 / MOON_RADIUS_KM / DEG);
        const hMax = WIDE_TYPES.indexOf(f.type) >= 0 ? BAND_WIDE_DEG : BAND_DEG;
        if (h < -r || h > hMax + r) return null;
        return {
            name: f.name, type: f.type, lat: f.lat, lon: f.lon, diameterKm: f.diameterKm, tier: f.tier,
            limb: f.limb, sunAlt: h, morning: M.isMorning(g, f.lat, f.lon),
            fromCentre: fromCentre, nearLimb: fromCentre > NEAR_LIMB_DEG,
        };
    }

    function rank(a, b) {
        return (a.tier - b.tier)
            || (Math.abs(a.sunAlt - IDEAL_SUN_DEG) - Math.abs(b.sunAlt - IDEAL_SUN_DEG))
            || (b.diameterKm - a.diameterKm)
            || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0);
    }

    function terminatorFeatures(g, illuminated, E) {
        if (illuminated < NEW_MOON_FRACTION) return [];
        const out = [];
        for (const f of E.F) {
            const hit = nearTerminator(f, g, E.M);
            if (hit) out.push(hit);
        }
        return out.sort(rank);
    }

    // ------------------------------------------------------------------ libration

    /** A feature's direction from the mean centre on the disc: 0 north, 90 the Mare Crisium side. */
    function directionOf(lat, lon) {
        return mod(Math.atan2(Math.sin(lon * DEG) * Math.cos(lat * DEG), Math.sin(lat * DEG)) / DEG, 360);
    }

    function levelOf(amount) {
        for (const [min, name] of LEVELS) if (amount >= min) return name;
        return 'none';
    }

    function hintWith(g, E) {
        const t = E.M.tilt(g);
        const visible = E.F.filter(f => f.limb && E.M.fromCentre(g, f.lat, f.lon) < 90)
            .map(f => ({ f: f, off: angleDiff(directionOf(f.lat, f.lon), t.direction) }));
        let named = visible.filter(v => v.off <= SECTOR_DEG)
            .sort((a, b) => (a.f.tier - b.f.tier) || (b.f.diameterKm - a.f.diameterKm));
        if (!named.length) named = visible.sort((a, b) => a.off - b.off).slice(0, 1);
        const isLit = f => E.M.sunAltitude(g, f.lat, f.lon) > 0;
        const side = named.length ? named[0].f : null;
        const lit = !!side && isLit(side);
        const level = levelOf(t.amount);
        return {
            amount: t.amount,
            direction: t.direction,
            level: level,
            side: side ? side.name : null,
            also: lit ? named.slice(1).filter(v => isLit(v.f)).slice(0, 2).map(v => v.f.name) : [],
            litLimb: lit,
            display: level === 'none' || lit ? level : 'dark',
        };
    }

    function hint(g) {
        return hintWith(g, engine());
    }

    // ------------------------------------------------------------------ tonight

    function tonight(opts) {
        const c = context(opts);
        const E = c.E, s = c.s;
        const moon = nightMoon(c.date, c, null);
        const g = E.M.geometry(moon.utcMs);
        const det = E.P.details('moon', moon.utcMs);
        const illuminated = finite(det.illuminated, 'moon illumination');
        const features = terminatorFeatures(g, illuminated, E);
        const muted = moon.maxAlt < s.threshold;
        let empty = null;
        if (!features.length) {
            empty = illuminated < NEW_MOON_FRACTION ? 'new' : illuminated > FULL_MOON_FRACTION ? 'full' : 'none';
        }
        return {
            date: c.date,
            instantUtc: moon.utcMs,
            hm: E.A.formatHM(moon.utcMs, s.tz),
            maxAlt: moon.maxAlt,
            threshold: s.threshold,
            muted: muted,
            mutedReason: !muted ? null : moon.maxAlt < 0 ? 'notUp' : 'altitude',
            geometry: g,
            illuminated: illuminated,
            features: features,
            top: features.slice(0, TOP_COUNT),
            more: features.slice(TOP_COUNT),
            empty: empty,
            libration: hintWith(g, E),
        };
    }

    function nextNights(opts) {
        const c = context(opts);
        const E = c.E, s = c.s;
        const d = parseDate(c.date);
        const sun = {};
        const nights = [];
        for (let k = 1; k <= NEXT_NIGHTS; k++) {
            const date = E.N.dateString(d.year, d.month, d.day + k);
            const moon = nightMoon(date, c, sun);
            if (moon.maxAlt < s.threshold) continue;
            const g = E.M.geometry(moon.utcMs);
            const illuminated = E.P.details('moon', moon.utcMs).illuminated;
            nights.push({ date: date, utcMs: moon.utcMs, g: g, illuminated: illuminated });
        }
        const byName = {};
        for (const f of E.F) {
            const dates = [];
            for (const n of nights) {
                if (dates.length >= NEXT_LIMIT) break;
                if (n.illuminated >= NEW_MOON_FRACTION && nearTerminator(f, n.g, E.M)) dates.push(n.date);
            }
            byName[f.name] = dates;
        }
        return { nights: nights.map(n => ({ date: n.date, utcMs: n.utcMs })), byName: byName };
    }

    // ------------------------------------------------------------------ calendar

    function lunarXCrossings(start, end, M) {
        const f = t => mod(M.geometry(t).colongitude - LUNAR_X_COLONGITUDE + 180, 360) - 180;
        const step = 12 * HOUR_MS;
        const out = [];
        let t0 = start, f0 = f(t0);
        for (let t1 = start + step; t1 <= end; t1 += step) {
            const f1 = f(t1);
            // Colongitude only grows (about 6 deg in 12 h): a crossing goes from - to +
            if (f0 < 0 && f1 >= 0 && f1 - f0 < 90) {
                let lo = t0, hi = t1;
                while (hi - lo > MINUTE_MS) {
                    const mid = (lo + hi) / 2;
                    if (f(mid) < 0) lo = mid; else hi = mid;
                }
                out.push(Math.round((lo + hi) / 2));
            }
            t0 = t1;
            f0 = f1;
        }
        return out;
    }

    function librationPeaks(start, end, M) {
        const f = t => M.tilt(M.geometry(t)).amount;
        const step = 6 * HOUR_MS;
        const out = [];
        let a = f(start), b = f(start + step);
        for (let t = start + 2 * step; t <= end; t += step) {
            const c = f(t);
            if (b > a && b >= c && b >= LIBRATION_EVENT_DEG - 0.5) {
                let lo = t - 2 * step, hi = t;
                while (hi - lo > MINUTE_MS) {
                    const m1 = hi - (hi - lo) * GOLDEN, m2 = lo + (hi - lo) * GOLDEN;
                    if (f(m1) > f(m2)) hi = m2; else lo = m1;
                }
                const utcMs = Math.round((lo + hi) / 2);
                if (f(utcMs) >= LIBRATION_EVENT_DEG) out.push(utcMs);
            }
            a = b;
            b = c;
        }
        return out;
    }

    function calendarRows(opts) {
        const c = context(opts);
        const E = c.E, A = E.A, P = E.P, W = E.W, N = E.N, s = c.s;
        const d = parseDate(c.date);
        const noonOf = dateStr => {
            const n = parseDate(dateStr);
            return W.localize(n.year, n.month, n.day, 12, 0, s.tz, A, false);
        };
        const win = {
            start: noonOf(N.dateString(d.year, d.month, d.day - DAYS_BACK)),
            end: noonOf(N.dateString(d.year, d.month + MONTHS_AHEAD, d.day)),
        };
        const today = dayNumber(d);
        const inWindow = t => t >= win.start && t <= win.end;
        const margin = 2 * DAY_MS;

        const found = [];
        for (const t of lunarXCrossings(win.start - margin, win.end + margin, E.M)) {
            if (inWindow(t)) found.push({ kind: 'lunarX', utcMs: t });
        }
        for (const t of librationPeaks(win.start - margin, win.end + margin, E.M)) {
            if (!inWindow(t)) continue;
            const h = hintWith(E.M.geometry(t), E);
            if (h.litLimb) found.push({ kind: 'libration', utcMs: t, libration: h });
        }
        found.sort((x, y) => x.utcMs - y.utcMs);

        const sun = {};
        const rows = found.map(ev => {
            const wall = A.localParts(ev.utcMs, s.tz);
            const night = W.observingNight(wall);
            const nd = parseDate(night);
            const nw = N.nightWindow(night, s, A, W, sun);
            const maxAlt = N.maxAltitude('moon', N.samples(nw.start, nw.end, s.interval), s, P);
            const det = P.details('moon', ev.utcMs);
            let muted, reason;
            if (ev.kind === 'lunarX') {
                let seen = false;
                for (let t = ev.utcMs - LUNAR_X_WINDOW_MS; t <= ev.utcMs + LUNAR_X_WINDOW_MS && !seen; t += s.interval * MINUTE_MS) {
                    seen = sunAltDeg(t, s.lat, s.lon, A) < A.SUN_RISE_SET_ALT
                        && P.altAz('moon', t, s.lat, s.lon).alt >= s.threshold;
                }
                muted = !seen;
                reason = muted ? 'lunarWindow' : null;
            } else {
                muted = maxAlt < s.threshold;
                reason = muted ? 'altitude' : null;
            }
            return {
                body: 'moon',
                bodies: ['moon'],
                kind: ev.kind,
                utcMs: ev.utcMs,
                night: night,
                hm: A.formatHM(ev.utcMs, s.tz),
                hmDay: Math.round(dayNumber(wall) - dayNumber(nd)),
                daysFromNow: Math.round(dayNumber(nd) - today),
                mag: finite(det.mag, 'moon magnitude'),
                diameterArcsec: finite(det.diameterArcsec, 'moon diameter'),
                elongationDeg: Math.abs(finite(det.elongationDeg, 'moon elongation')),
                maxAlt: maxAlt,
                muted: muted,
                mutedReason: reason,
                sepDeg: null,
                other: null,
                moment: null,
                occultation: false,
                ringTiltDeg: null,
                closestApproach: null,
                libration: ev.libration || null,
            };
        });
        return { window: win, rows: rows };
    }

    // ------------------------------------------------------------------ diagram

    function viewOf(mode) {
        if (VIEWS.indexOf(mode) < 0) throw new Error('nova_solar_moon: unknown view ' + JSON.stringify(mode));
        // From the disc (x to the Mare Crisium side, y north) to the view, then SVG (y down)
        const sx = mode === 'normal' ? 1 : -1, sy = mode === 'inverted' ? 1 : -1;
        return (x, y) => ({ x: sx * x, y: sy * y });
    }

    const r6 = x => Math.round(x * 1e6) / 1e6;

    /** The lit part of a unit disc: u the unit vector towards the bright limb (SVG
     *  coordinates), k the cosine of the Earth-Moon-Sun angle (1 full, -1 new). The
     *  limb half on u's side, closed by the terminator: a half ellipse at -k along u. */
    function litPath(ux, uy, k) {
        if (k >= 0.9995) return { lit: 'M 0 -1 A 1 1 0 1 1 0 1 A 1 1 0 1 1 0 -1 Z', terminator: null };
        if (k <= -0.9995) return { lit: null, terminator: null };
        const vx = -uy, vy = ux;
        const rot = r6(Math.atan2(uy, ux) / DEG);
        const p1 = r6(vx) + ' ' + r6(vy), p2 = r6(-vx) + ' ' + r6(-vy);
        const rx = r6(Math.abs(k));
        const term = 'A ' + rx + ' 1 ' + rot + ' 0 ' + (k > 0 ? 0 : 1) + ' ' + p1;
        return {
            lit: 'M ' + p1 + ' A 1 1 0 0 0 ' + p2 + ' ' + term + ' Z',
            terminator: 'M ' + p2 + ' ' + term,
        };
    }

    function diagram(g, mode, features) {
        const E = engine();
        const view = viewOf(mode);
        const sp = E.M.project(g, g.subsolar.lat, g.subsolar.lon);
        const len = Math.hypot(sp.x, sp.y);
        const u = view(len > 1e-9 ? sp.x / len : 1, len > 1e-9 ? sp.y / len : 0);
        const paths = litPath(u.x, u.y, Math.max(-1, Math.min(1, sp.z)));
        const markers = [];
        (features || []).forEach((f, i) => {
            const p = E.M.project(g, f.lat, f.lon);
            if (p.z > 0) {
                const q = view(p.x, p.y);
                markers.push({ x: r6(q.x), y: r6(q.y), n: i + 1, name: f.name });
            }
        });
        const t = E.M.tilt(g);
        const arrow = view(Math.sin(t.direction * DEG), Math.cos(t.direction * DEG));
        const np = E.M.project(g, 90, 0);
        const north = view(np.x, np.y);
        return {
            lit: paths.lit, terminator: paths.terminator, markers: markers,
            arrow: { x: r6(arrow.x), y: r6(arrow.y) }, north: { x: r6(north.x), y: r6(north.y) },
        };
    }

    return {
        VISIBLE_DEG: VISIBLE_DEG, NEAR_LIMB_DEG: NEAR_LIMB_DEG, BAND_DEG: BAND_DEG, RADIUS_CAP_DEG: RADIUS_CAP_DEG,
        BAND_WIDE_DEG: BAND_WIDE_DEG, WIDE_TYPES: WIDE_TYPES, IDEAL_SUN_DEG: IDEAL_SUN_DEG,
        TOP_COUNT: TOP_COUNT, NEW_MOON_FRACTION: NEW_MOON_FRACTION, FULL_MOON_FRACTION: FULL_MOON_FRACTION,
        LEVELS: LEVELS, SECTOR_DEG: SECTOR_DEG, NEXT_NIGHTS: NEXT_NIGHTS,
        NEXT_LIMIT: NEXT_LIMIT, LUNAR_X_COLONGITUDE: LUNAR_X_COLONGITUDE, LUNAR_X_WINDOW_MS: LUNAR_X_WINDOW_MS,
        LIBRATION_EVENT_DEG: LIBRATION_EVENT_DEG, VIEWS: VIEWS,
        tonight: tonight, hint: hint, nextNights: nextNights, calendarRows: calendarRows, diagram: diagram,
        directionOf: directionOf, levelOf: levelOf,
    };
});
