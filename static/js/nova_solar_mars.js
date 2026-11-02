/*
 * nova_solar_mars.js — the Solar System page's Mars card: the disc as it stands tonight,
 * the features the IAU names that are on it, and when each of them crosses the central
 * meridian.
 *
 * No DOM: the page (static/js/solar_system.js) draws and formats everything.
 *
 * Built on the shared engines and the page's own night rules, looked up as globals at
 * call time, so the page only has to load them first:
 *   NovaAstro         (static/js/nova_astro.js)           time zones, sun events
 *   NovaPlanets       (static/js/nova_planets.js)         the bodies
 *   NovaMars          (static/js/nova_mars.js)            the globe and its rotation
 *   NovaMarsFeatures  (static/js/nova_mars_features.js)   the IAU names and their centres
 *   NovaDashboardRows (static/js/nova_dashboard_rows.js)  the instant and the night
 *   NovaSolarRows     (static/js/nova_solar_rows.js)      night window and its maximum
 *
 * opts, as every card module on this page: `inputs` (the /api/desktop_inputs response for
 * the location), `nowMs` (Date.now(), UTC ms) and `simDate` ('YYYY-MM-DD' or null).
 *
 * The features are the IAU Gazetteer's centre points, planetocentric latitude and
 * east-positive longitude (tools/gen_mars_features.py). NovaMars.geometry().cm is the
 * other convention — planetographic, west-positive, what JPL Horizons prints as
 * ObsSub-LON — so the sub-Earth point is at east longitude -cm and a feature is on the
 * central meridian when cm = 360 - lon. Both conversions are made here, once, so the
 * page only ever sees east-positive longitudes beside a cm it prints as it stands.
 *
 * tonight(opts) -> {
 *   date, instantUtc, hm,          the observing night and its best instant (that night's
 *                                  highest sample, the midpoint when Mars is below the
 *                                  horizon all night), that instant as 'HH:MM'
 *   maxAlt, threshold, muted, mutedReason,   as the other cards: 'altitude', or 'notUp'
 *                                  when it never rises
 *   geometry,                      NovaMars.geometry() at the instant: cm, de, ds, polePA,
 *                                  sunPA, phaseAngleDeg, distanceAU, diameterArcsec
 *   illuminated, mag,              from NovaPlanets.details('mars') at the instant
 *   features,                      every named feature on the visible disc, ranked: tier
 *                                  first, then how close to the central meridian, then
 *                                  size. Each: { name, type, lat, lon, diameterKm, tier,
 *                                  x, y, gamma, cmDist, nearLimb, nextUtcMs, nextHm,
 *                                  nextHmDay }. x and y are on the disc in the drawing's
 *                                  frame (below), gamma the degrees from the centre of the
 *                                  disc, cmDist the signed degrees from the central
 *                                  meridian (its sign is x's), and nextUtcMs the next
 *                                  time the feature crosses it — nextHmDay being that
 *                                  passage's local date counted in days from the instant
 *                                  shown (0 today, 1 tomorrow)
 *   top, more,                     the first TOP_COUNT of those, and the rest
 *   empty,                         'none' when nothing named is on the disc, else null
 * }
 *
 * diagram(g, mode, features) -> Mars on a unit disc in SVG coordinates (y down), drawn
 *   as the eyepiece has it: celestial north up, so the pole leans by polePA and its disc
 *   is round the way the sky turns it. In that frame:
 *     shaded      SVG path of the unlit part — Mars's phase runs to 47 degrees, worth
 *                 drawing — or null when the disc is full
 *     terminator  its edge
 *     markers     [{ x, y, n, name }] for `features`, 1-based
 *     pole        unit vector from the centre towards Mars's north pole, so the page can
 *                 mark which way the pole leans; `tilt` is De, its own angle from the top
 *   The lit-part construction is the Moon card's (nova_solar_moon.js), the same three
 *   views: 'normal' is the sky as it stands, 'diagonal' mirrors the disc left and right
 *   (a star diagonal) and 'inverted' turns it 180 degrees (a Newtonian, or a refractor
 *   without a diagonal).
 *
 * Everything throws on a missing location, a non-finite coordinate or any engine failure;
 * the page shows its error instead.
 */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.NovaSolarMars = factory();
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const MINUTE_MS = 60000;
    const DAY_MS = 86400000;
    const DEG = Math.PI / 180;

    const VIEWS = Object.freeze(['normal', 'diagonal', 'inverted']);
    const VISIBLE_DEG = 85;             // past this a feature is at the very limb
    const NEAR_LIMB_DEG = 75;
    const TOP_COUNT = 8;
    const CM_STEP_MS = 10 * MINUTE_MS;

    function engine() {
        const A = typeof NovaAstro !== 'undefined' ? NovaAstro : null;
        const P = typeof NovaPlanets !== 'undefined' ? NovaPlanets : null;
        const M = typeof NovaMars !== 'undefined' ? NovaMars : null;
        const F = typeof NovaMarsFeatures !== 'undefined' ? NovaMarsFeatures : null;
        const R = typeof NovaDashboardRows !== 'undefined' ? NovaDashboardRows : null;
        const S = typeof NovaSolarRows !== 'undefined' ? NovaSolarRows : null;
        if (!A || !P || !M || !F || !R || !S) {
            throw new Error('nova_solar_mars: nova_astro.js, nova_planets.js, nova_mars.js, '
                + 'nova_mars_features.js, nova_dashboard_rows.js and nova_solar_rows.js must be loaded first');
        }
        return { A: A, P: P, M: M, F: F.FEATURES, W: R._wall, N: S._night };
    }

    function finite(x, what) {
        const v = typeof x === 'number' ? x : NaN;
        if (!isFinite(v)) throw new Error('nova_solar_mars: ' + what + ' is not a finite number');
        return v;
    }

    function parseDate(dateStr) {
        const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(dateStr));
        if (!m) throw new Error('nova_solar_mars: date must be YYYY-MM-DD, got ' + JSON.stringify(dateStr));
        return { year: +m[1], month: +m[2], day: +m[3] };
    }

    const dayNumber = d => Date.UTC(d.year, d.month - 1, d.day) / DAY_MS;
    const mod = (a, n) => ((a % n) + n) % n;

    /** The current instant, the observing night and the site, as the Bodies tab has them. */
    function context(opts) {
        const o = opts || {};
        const E = engine();
        const s = E.N.site(o.inputs);
        const now = E.W.effectiveInstant(finite(o.nowMs, 'nowMs'), o.simDate, s.tz, E.A);
        return { E: E, s: s, now: now, date: E.W.observingNight(now.wall) };
    }

    /** The night's Mars: its highest sample in the night window (the midpoint when it is
     *  below the horizon all night), as the other cards. */
    function nightMars(date, c, sun) {
        const E = c.E, s = c.s;
        const win = E.N.nightWindow(date, s, E.A, E.W, sun);
        const times = E.N.samples(win.start, win.end, s.interval);
        const curve = E.P.altAzCurve('mars', times, s.lat, s.lon);
        let k = 0;
        for (let i = 1; i < curve.alt.length; i++) if (curve.alt[i] > curve.alt[k]) k = i;
        const maxAlt = finite(curve.alt[k], 'mars altitude');
        const utcMs = maxAlt >= 0 ? times[k] : Math.round((win.start + win.end) / 2);
        return { window: win, utcMs: utcMs, maxAlt: maxAlt };
    }

    // ------------------------------------------------------------------ the disc

    /** The drawn position of a surface point: x along the equator, y towards the pole —
     *  Mars's own frame, so the pole is up however polePA turns the disc on the sky. The
     *  sub-Earth point is at (de, -cm) in the feature file's convention, so a point at
     *  (lat, lon) is at x = sin(lon + cm), y = sin(lat)cos(de) - cos(lat)sin(de)cos(lon + cm)
     *  times cos(lat) on x, and it faces us when its angular distance from the sub-Earth
     *  point, gamma, is under 90 degrees. */
    function project(lat, lon, de, cm) {
        const la = finite(lat, 'feature latitude') * DEG;
        const d = lon * DEG + cm * DEG;                  // longitude from the sub-Earth point
        const deR = de * DEG;
        const cosGamma = Math.sin(la) * Math.sin(deR) + Math.cos(la) * Math.cos(deR) * Math.cos(d);
        return {
            x: Math.cos(la) * Math.sin(d),
            y: Math.sin(la) * Math.cos(deR) - Math.cos(la) * Math.sin(deR) * Math.cos(d),
            gamma: Math.acos(Math.max(-1, Math.min(1, cosGamma))) / DEG,
        };
    }

    /**
     * When each feature next crosses the central meridian: a table of cm over the next
     * rotation, then a bisection per feature on the wrap-around difference from that
     * feature's longitude. The meridian comes back to any longitude within one rotation,
     * 360/|W_RATE| days of the engine's own IAU value, and the window is a tenth longer.
     */
    function meridianPassages(fromMs, features, E) {
        const span = 1.1 * (360 / Math.abs(E.M.W_RATE)) * DAY_MS;
        const times = [], cms = [];
        for (let t = fromMs; t <= fromMs + span; t += CM_STEP_MS) {
            times.push(t);
            cms.push(finite(E.M.geometry(t).cm, 'mars central meridian'));
        }
        const f = (t, target) => mod(E.M.geometry(t).cm - target + 180, 360) - 180;
        return features.map(feature => {
            const target = mod(360 - feature.lon, 360);              // cm on the meridian then
            let k = 0;
            while (k + 1 < times.length && !(mod(cms[k] - target + 180, 360) - 180 < 0
                && mod(cms[k + 1] - target + 180, 360) - 180 >= 0)) k++;
            if (k + 1 >= times.length) return null;
            let lo = times[k], hi = times[k + 1];
            const loNeg = f(lo, target) < 0;
            while (hi - lo > MINUTE_MS) {
                const mid = Math.round((lo + hi) / 2);
                if (mid === lo || mid === hi) break;
                if ((f(mid, target) < 0) === loNeg) lo = mid; else hi = mid;
            }
            return Math.round((lo + hi) / 2);
        });
    }

    function tonight(opts) {
        const c = context(opts);
        const E = c.E, s = c.s;
        const night = nightMars(c.date, c, {});
        const g = E.M.geometry(night.utcMs);
        const det = E.P.details('mars', night.utcMs);
        const today = dayNumber(E.A.localParts(night.utcMs, s.tz));

        // What is on the disc, and where
        const onDisc = [];
        for (const f of E.F) {
            const p = project(f.lat, f.lon, g.de, g.cm);
            if (!(p.gamma < VISIBLE_DEG)) continue;
            onDisc.push({
                name: f.name, type: f.type, lat: f.lat, lon: f.lon,
                diameterKm: f.diameterKm, tier: f.tier,
                x: p.x, y: p.y, gamma: p.gamma,
                // The signed degrees from the central meridian: the sign is x's
                cmDist: mod(f.lon + g.cm + 180, 360) - 180,
                nearLimb: p.gamma > NEAR_LIMB_DEG,
            });
        }
        onDisc.sort((a, b) => (a.tier - b.tier) || (Math.abs(a.cmDist) - Math.abs(b.cmDist))
            || (b.diameterKm - a.diameterKm) || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
        const passages = meridianPassages(night.utcMs, onDisc, E);
        onDisc.forEach((f, i) => {
            const t = passages[i];
            f.nextUtcMs = t;
            f.nextHm = t === null ? null : E.A.formatHM(t, s.tz);
            f.nextHmDay = t === null ? null
                : Math.round(dayNumber(E.A.localParts(t, s.tz)) - today);
        });

        const muted = night.maxAlt < s.threshold;
        return {
            date: c.date,
            instantUtc: night.utcMs,
            hm: E.A.formatHM(night.utcMs, s.tz),
            maxAlt: night.maxAlt,
            threshold: s.threshold,
            muted: muted,
            mutedReason: !muted ? null : night.maxAlt < 0 ? 'notUp' : 'altitude',
            geometry: g,
            illuminated: finite(det.illuminated, 'mars illumination'),
            mag: finite(det.mag, 'mars magnitude'),
            features: onDisc,
            top: onDisc.slice(0, TOP_COUNT),
            more: onDisc.slice(TOP_COUNT),
            empty: onDisc.length ? null : 'none',
        };
    }

    // ------------------------------------------------------------------ diagram

    function viewOf(mode) {
        if (VIEWS.indexOf(mode) < 0) throw new Error('nova_solar_mars: unknown view ' + JSON.stringify(mode));
        // From the sky as it stands (x right, y up, celestial north up) to SVG (y down):
        // 'normal' is that view, 'diagonal' mirrors it left and right (a star diagonal),
        // 'inverted' turns it the other 180 degrees (a Newtonian). The y is what carries
        // the sky's handedness: negating it is the whole of "north is up", and the same
        // one line as in nova_solar_moon.js, whose frame is Mars's own.
        const sx = mode === 'normal' ? 1 : -1, sy = mode === 'inverted' ? 1 : -1;
        return (x, y) => ({ x: sx * x, y: sy * y });
    }

    const r6 = x => Math.round(x * 1e6) / 1e6;

    /** The lit part of a unit disc, the Moon card's construction exactly (litPath in
     *  nova_solar_moon.js): u the unit vector towards the bright limb (SVG coordinates),
     *  k the cosine of the phase angle (1 full, -1 new). The limb half on u's side, closed
     *  by the terminator: a half ellipse at -k along u. Mars's *dark* part is what gets
     *  drawn over the disc, so diagram() calls this with u and k both negated. */
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
        const view = viewOf(mode);
        const pa = finite(g.polePA, 'polePA') * DEG;
        // project() gives the disc in Mars's own frame, x along its equator (the side
        // features drift towards) and y at the pole. The sky turns that frame by polePA:
        // rotating by it moves the pole to where it actually stands on the sky, which is
        // what the eyepiece shows. (An orthographic projection is faithful either way —
        // this only decides which way up the round disc is drawn.)
        const of = (x, y) => Math.cos(pa) * x - Math.sin(pa) * y;
        const at = (x, y) => Math.sin(pa) * x + Math.cos(pa) * y;
        const sky = (x, y) => ({ x: of(x, y), y: at(x, y) });
        // The Sun's position angle is already a sky angle, so the bright limb needs no
        // pole term: it is the sky direction at sunPA, and the terminator follows. The
        // disc shows the dark part, so litPath gets u and k negated.
        const su = finite(g.sunPA, 'sunPA') * DEG;
        const u = view(-Math.sin(su), Math.cos(su));
        const k = Math.cos(finite(g.phaseAngleDeg, 'phase angle') * DEG);
        const path = litPath(-u.x, -u.y, -k);
        const pole = view(sky(0, 1).x, sky(0, 1).y);
        return {
            shaded: path.lit,
            terminator: path.terminator,
            markers: (features || []).map((f, i) => {
                const p = sky(f.x, f.y);
                const q = view(p.x, p.y);
                return { x: r6(q.x), y: r6(q.y), n: i + 1, name: f.name };
            }),
            pole: { x: r6(pole.x), y: r6(pole.y) },
            tilt: finite(g.de, 'De'),
        };
    }

    return {
        VIEWS: VIEWS, VISIBLE_DEG: VISIBLE_DEG, NEAR_LIMB_DEG: NEAR_LIMB_DEG, TOP_COUNT: TOP_COUNT,
        tonight: tonight, diagram: diagram,
    };
});
