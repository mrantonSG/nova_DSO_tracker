/*
 * nova_solar_inner.js — the Solar System page's Venus and Mercury cards: the disc as it
 * stands tonight and the twilight window it is worth looking at.
 *
 * No DOM: the page (static/js/solar_system.js) draws and formats everything.
 *
 * Built on the shared engines and the page's own night rules, looked up as globals at
 * call time, so the page only has to load them first:
 *   NovaAstro         (static/js/nova_astro.js)           sun events, time zones, frames
 *   NovaPlanets       (static/js/nova_planets.js)         the bodies: place, phase, size
 *   NovaDashboardRows (static/js/nova_dashboard_rows.js)  the instant and the night
 *   NovaSolarRows     (static/js/nova_solar_rows.js)      night window and its samples
 *
 * opts, as every card module on this page: `inputs` (the /api/desktop_inputs response for
 * the location), `nowMs` (Date.now(), UTC ms) and `simDate` ('YYYY-MM-DD' or null).
 * The body is named first, as NovaPlanets.details names it: tonight(body, opts).
 *
 * ------------------------------------------------------------------ the two angles
 *
 * A phase drawing needs two position angles, both north through east, and both are the
 * same piece of geometry on different directions:
 *
 *   sunPA   the bright limb's midpoint, the Moon card's chi (nova_moon.js:186-188,
 *           Meeus, Astronomical Algorithms 2nd ed., ch. 48.5), taken against the Sun
 *           instead of the Moon.
 *   polePA  the planet's own north pole, so the disc can be drawn with its own north up.
 *
 * The block below — pole(), positionAngleOfDate(), positionAngle() — is copied from
 * nova_mars.js:162-189, where it was validated against JPL Horizons' NP.ang; the same
 * block is repeated in nova_jupiter.js:308 and nova_saturn.js:271. The three copies in
 * the shared engines can only be changed in nova-ios (tests/test_shared_js.py pins them
 * by sha256), and this is two bodies' worth of constants, so it is copied again here
 * rather than moved. The reduction is theirs exactly: a position angle belongs to a
 * frame, so both the pole and the body's place are carried from J2000 to the equator of
 * date with NovaAstro.frameFor().M before the angle is taken.
 *
 * The poles, IAU/WGCCRE (Archinal et al. 2018), the values SPICE pck00011.tpc carries as
 * BODY199_POLE_RA / BODY199_POLE_DEC and BODY299_POLE_RA / BODY299_POLE_DEC, degrees and
 * degrees per Julian century:
 *
 *   Mercury   alpha0 = 281.0103 - 0.03242 T     delta0 = 61.4155 - 0.00434 T
 *   Venus     alpha0 = 272.76 (no T term)       delta0 = 67.16 (no T term)
 *
 * Checked against Horizons quantity 17 (NP.ang, counter-clockwise from the true-of-date
 * north) at 157 dates over 2026-2028 for both bodies, the extract in
 * tests/fixtures/horizons/: worst 0.007 deg, median 0.002 deg.
 * Both poles sweep the full 0-360 deg over two years, because each planet's right
 * ascension does, so this is a real rotation of the drawing and not a formality. Both
 * bodies are seen very nearly equator-on (Horizons q14 sub-observer latitude, 2026-2028:
 * Mercury -10.7 to +11.1 deg, Venus -5.8 to +8.8 deg), so there is no open pole and no
 * ring plane to handle: the disc is a circle with a terminator across it.
 *
 * ------------------------------------------------------------------ the shared scale
 *
 * The two discs are drawn to one scale, so that they can be compared on the page and so
 * that a crescent Venus reads as big. It is a linear map of the apparent diameter onto
 * the drawn radius, from the smallest Mercury to the largest Venus, which are the
 * published extremes and not this window's: Mercury 4.5 to 13.0 arcsec and Venus 9.7 to
 * 66.1 arcsec (NASA NSSDCA planetary fact sheets; Universe Today's values agree). The
 * map is affine rather than strictly proportional — proportionality would put Mercury at
 * a fourteenth of Venus and a sixth of a pixel-per-arcsec radius, which draws it as a
 * speck and makes its own phase unreadable. The order and the progression are kept; only
 * the ratio is compressed. RADIUS_MIN_PX is the radius at 4.5 arcsec and RADIUS_MAX_PX
 * the radius at 66.1 arcsec.
 *
 * ------------------------------------------------------------------ tonight(body, opts)
 *
 * {
 *   body, date, instantUtc, hm,   the body, the observing night and its best instant
 *                                 (that night's highest sample, the midpoint when the
 *                                 planet is below the horizon all night), as 'HH:MM'
 *   maxAlt, threshold, muted, mutedReason,   as the other cards: 'altitude', or 'notUp'
 *                                 when it never rises
 *   phaseAngleDeg, illuminated, mag, diameterArcsec, elongationDeg, elongationSide,
 *                                 from NovaPlanets.details() at the instant
 *   polePA, sunPA,                the two position angles above, north through east
 *   scale,                        the drawn radius as a fraction of the drawing's own
 *                                 unit disc (see the shared scale above); 1 is the size
 *                                 Mars's card is drawn at
 *   best,                         { startUtcMs, endUtcMs, startHm, endHm, minutes, samples,
 *                                 side }: the longest run of samples inside the night
 *                                 window at or above the threshold, both ends being samples
 *                                 that are up, so what the card shows is always a time the
 *                                 planet is workable. It is the same run of samples the
 *                                 instant above is taken from, so that instant is always
 *                                 inside the window; `samples` is how many samples the run
 *                                 is, and it is 1 whenever the stretch above the threshold
 *                                 is shorter than one sampling interval — a window of no
 *                                 width, which the card shows as that single time. `side`
 *                                 is 'evening' or 'morning' by
 *                                 which half of the local day the run sits in. Null when
 *                                 the planet is never up in the night
 *   daylightOnly,                 true when the planet reaches the threshold in the
 *                                 observing day but never inside the night window: a
 *                                 daytime target, which is the honest answer for these
 *                                 two and the reason the card has the state at all. It is
 *                                 also true at a site where the Sun does not set, where
 *                                 the noon-to-noon window nova_solar_rows.js falls back
 *                                 to is sunlight throughout and there is no night to name
 * }
 *
 * diagram(t, mode) -> the disc on a unit disc in SVG coordinates (y down), drawn with the
 *   planet's own north up, as the Moon, Mars, Jupiter and Saturn cards are: x along its
 *   equator, y at its pole, so the sky turns that frame by polePA and every sky direction
 *   (the Sun, celestial north) is brought into it at its position angle less polePA. In
 *   that frame:
 *     shaded      SVG path of the unlit part — for these two that runs from a full disc
 *                 at superior conjunction to nothing at all at inferior — or null when
 *                 the disc is full
 *     terminator  its edge, null when there is none
 *     pole        unit vector from the centre towards the planet's north pole (straight
 *                 up in 'normal')
 *     north       unit vector towards celestial north, at position angle 0 - polePA, for
 *                 the page's north arrow
 *   The lit-part construction is the Moon card's (litPath in nova_solar_moon.js), the same
 *   three views: 'normal' is the figure as it stands, 'diagonal' mirrors the disc left and
 *   right (a star diagonal) and 'inverted' turns it 180 degrees (a Newtonian, or a
 *   refractor without a diagonal). The whole figure is drawn on the unit disc whatever
 *   `scale` is; the page scales it.
 *
 * Everything throws on an unknown body, a missing location, a non-finite coordinate or any
 * engine failure; the page shows its error instead.
 */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.NovaSolarInner = factory();
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const PI = Math.PI;
    const DEG = PI / 180, HOUR = PI / 12;
    const DAY_MS = 86400000;

    const BODIES = Object.freeze(['venus', 'mercury']);
    const VIEWS = Object.freeze(['normal', 'diagonal', 'inverted']);

    // The shared disc scale (see the header). The extremes are the published ones, not
    // this window's, so the drawing does not resize as the years pass.
    const DIAM_MIN_ARCSEC = 4.5;        // Mercury at its smallest
    const DIAM_MAX_ARCSEC = 66.1;       // Venus at its largest
    const RADIUS_MIN_PX = 7;            // the drawn radius at DIAM_MIN_ARCSEC
    const RADIUS_MAX_PX = 46;           // the drawn radius at DIAM_MAX_ARCSEC, and the
                                        // radius the drawing's unit disc stands for

    // J2000 mean obliquity, the same constant nova_mars.js carries.
    const EPS_J2000 = 23.4392911 * DEG;

    // TT - UTC, 69.184 s since 2017-01-01. The IAU rotation formulas take their
    // independent variable in TDB, epoch J2000; it is 2e-8 centuries in the pole's T.
    const TT_MINUS_UTC_MS = 69184;

    // IAU/WGCCRE poles, degrees and degrees per Julian century.
    const POLES = {
        mercury: { ra0: 281.0103, raRate: -0.03242, dec0: 61.4155, decRate: -0.00434 },
        venus: { ra0: 272.76, raRate: 0, dec0: 67.16, decRate: 0 },
    };

    function engine() {
        const A = typeof NovaAstro !== 'undefined' ? NovaAstro : null;
        const P = typeof NovaPlanets !== 'undefined' ? NovaPlanets : null;
        const R = typeof NovaDashboardRows !== 'undefined' ? NovaDashboardRows : null;
        const S = typeof NovaSolarRows !== 'undefined' ? NovaSolarRows : null;
        if (!A || !P || !R || !S) {
            throw new Error('nova_solar_inner: nova_astro.js, nova_planets.js, '
                + 'nova_dashboard_rows.js and nova_solar_rows.js must be loaded first');
        }
        return { A: A, P: P, W: R._wall, N: S._night };
    }

    function checkBody(body) {
        const b = String(body);
        if (BODIES.indexOf(b) < 0) {
            throw new Error('nova_solar_inner: unknown body ' + JSON.stringify(body));
        }
        return b;
    }

    function finite(x, what) {
        const v = typeof x === 'number' ? x : NaN;
        if (!isFinite(v)) throw new Error('nova_solar_inner: ' + what + ' is not a finite number');
        return v;
    }

    function parseDate(dateStr) {
        const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(dateStr));
        if (!m) throw new Error('nova_solar_inner: date must be YYYY-MM-DD, got ' + JSON.stringify(dateStr));
        return { year: +m[1], month: +m[2], day: +m[3] };
    }

    const sin = Math.sin, cos = Math.cos;
    const mod = (a, n) => ((a % n) + n) % n;
    const clamp1 = x => (x > 1 ? 1 : x < -1 ? -1 : x);
    const centuries = utcMs => (utcMs / DAY_MS + 2440587.5 - 2451545.0) / 36525;

    const norm = v => Math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
    const apply = (M, v) => [
        M[0] * v[0] + M[1] * v[1] + M[2] * v[2],
        M[3] * v[0] + M[4] * v[1] + M[5] * v[2],
        M[6] * v[0] + M[7] * v[1] + M[8] * v[2],
    ];

    // ------------------------------------------------------------------ the two angles

    /** Unit vector Earth -> body, ICRF/J2000 astrometric. */
    function earthToBody(body, utcMs) {
        const p = engine().P.position(body, utcMs);
        const ra = p.raHours * HOUR, dec = p.decDeg * DEG, cd = cos(dec);
        return [cd * cos(ra), cd * sin(ra), sin(dec)];
    }

    /**
     * Unit vector Earth -> Sun, ICRF/J2000. The same reduction as nova_mars.js: the Sun's
     * geometric longitude on the mean equinox of date (sunPosition().trueLon), less the
     * precession in longitude, then the ecliptic of J2000 into the equator of J2000. The
     * geometric place and not the apparent one, because the apparent carries the annual
     * aberration - an observing artefact, not a shift of the Sun. Note trueLon is in
     * RADIANS while the precession series is in degrees.
     */
    function earthToSun(utcMs) {
        const A = engine().A;
        const s = A.sunPosition(utcMs);
        const T = centuries(utcMs);
        const prec = (5029.0966 * T + 1.11113 * T * T - 0.000006 * T * T * T) / 3600;
        const lam = s.trueLon - prec * DEG;                // radians
        return [cos(lam), sin(lam) * cos(EPS_J2000), sin(lam) * sin(EPS_J2000)];
    }

    /** The body's pole direction, ICRF/J2000, from the IAU/WGCCRE elements above. */
    function pole(body, utcMs) {
        const p = POLES[body];
        const T = centuries(utcMs + TT_MINUS_UTC_MS);
        const ra = (p.ra0 + p.raRate * T) * DEG;
        const dec = (p.dec0 + p.decRate * T) * DEG;
        return [cos(dec) * cos(ra), cos(dec) * sin(ra), sin(dec)];
    }

    /** Position angle of a J2000 direction v against the body's place u, both turned to
     *  the equator of date: a position angle belongs to a frame, and the pole of the date
     *  frame is not the pole of J2000 (nova_mars.js:169-171). */
    function positionAngleOfDate(v, u, utcMs) {
        const M = engine().A.frameFor(utcMs).M;
        return positionAngle(apply(M, v), apply(M, u));
    }

    /** Position angle of a direction v against the body (u), north through east. */
    function positionAngle(v, u) {
        const raJ = Math.atan2(u[1], u[0]), decJ = Math.asin(clamp1(u[2]));
        const ra = Math.atan2(v[1], v[0]), dec = Math.asin(clamp1(v[2]));
        return mod(Math.atan2(cos(dec) * sin(ra - raJ),
            sin(dec) * cos(decJ) - cos(dec) * sin(decJ) * cos(ra - raJ)) / DEG, 360);
    }

    /** The bright limb's midpoint and the planet's own north pole, north through east. */
    function angles(body, utcMs) {
        const u = earthToBody(body, utcMs);
        return {
            sunPA: positionAngleOfDate(earthToSun(utcMs), u, utcMs),
            polePA: positionAngleOfDate(pole(body, utcMs), u, utcMs),
        };
    }

    // ------------------------------------------------------------------ the night

    /** The drawn radius as a fraction of the drawing's unit disc. */
    function scaleOf(diameterArcsec) {
        const d = finite(diameterArcsec, 'apparent diameter');
        const r = RADIUS_MIN_PX + (d - DIAM_MIN_ARCSEC) * (RADIUS_MAX_PX - RADIUS_MIN_PX)
            / (DIAM_MAX_ARCSEC - DIAM_MIN_ARCSEC);
        return r / RADIUS_MAX_PX;
    }

    /**
     * The longest run of samples at or above the threshold that lies inside the night
     * window, as a half-open pair of sample indices, or null. The samples are the night's
     * own — the ones tonight() takes its instant from — so a sample in the run is one the
     * card can name, and the instant it names is one of them.
     */
    function longestRun(times, alt, threshold, night) {
        let best = null, start = -1;
        for (let i = 0; i <= times.length; i++) {
            const inNight = i < times.length && times[i] >= night.start && times[i] <= night.end;
            const up = i < times.length && alt[i] >= threshold && inNight;
            if (up && start < 0) start = i;
            if (!up && start >= 0) {
                if (!best || i - start > best[1] - best[0]) best = [start, i];
                start = -1;
            }
        }
        return best;
    }

    /** The best window, as the card shows it, or null. */
    function bestWindow(times, alt, s, night, A) {
        const run = longestRun(times, alt, s.threshold, night);
        if (!run) return null;
        // Both ends are samples that are up: the window is the run itself, the first and
        // the last instant the planet is at or above the threshold inside the night. The
        // true set-and-rise window is up to one sampling interval wider at each end, and
        // naming the sampled run is the honest way round — every time the card shows is a
        // time the planet is above the threshold, and none of them is daylight. A run of
        // one sample is what a stretch above the threshold shorter than one sampling
        // interval comes to — likelier the coarser the precision, but it happens at any of
        // them — and the card then shows the one time it is: a range from a time to itself
        // would say nothing.
        const samples = run[1] - run[0];
        const startUtcMs = times[run[0]];
        const endUtcMs = times[run[1] - 1];
        const mid = (startUtcMs + endUtcMs) / 2;
        return {
            startUtcMs: startUtcMs,
            endUtcMs: endUtcMs,
            startHm: A.formatHM(startUtcMs, s.tz),
            endHm: A.formatHM(endUtcMs, s.tz),
            minutes: Math.round((endUtcMs - startUtcMs) / 60000),
            samples: samples,
            // Which half of the local day it sits in: an inner planet is an evening
            // apparition or a morning one, and never usefully both in the same night.
            side: A.localParts(mid, s.tz).hour >= 12 ? 'evening' : 'morning',
        };
    }

    function tonight(body, opts) {
        const o = opts || {};
        const b = checkBody(body);
        const E = engine(), A = E.A, W = E.W, N = E.N;
        const s = N.site(o.inputs);
        const now = W.effectiveInstant(finite(o.nowMs, 'nowMs'), o.simDate, s.tz, A);
        const date = W.observingNight(now.wall);

        const night = N.nightWindow(date, s, A, W, null);
        // The whole observing day, local noon to the next: the night window sits inside
        // it, and the rest of it is what the daylight answer is about.
        const d = parseDate(date);
        const noon = W.localize(d.year, d.month, d.day, 12, 0, s.tz, A, false);

        const nightTimes = N.samples(night.start, night.end, s.interval);
        const curve = E.P.altAzCurve(b, nightTimes, s.lat, s.lon);
        let k = 0;
        for (let i = 1; i < curve.alt.length; i++) if (curve.alt[i] > curve.alt[k]) k = i;
        const maxAlt = finite(curve.alt[k], b + ' altitude');
        const instantUtc = maxAlt >= 0 ? nightTimes[k] : Math.round((night.start + night.end) / 2);

        const det = E.P.details(b, instantUtc);
        const phaseAngleDeg = finite(det.phaseAngleDeg, b + ' phase angle');
        const ang = angles(b, instantUtc);

        // The Sun's declination at local noon, for the polar-day test below: sunPosition()
        // gives it in radians.
        const polarDay = night.kind === 'noon'
            && s.lat + A.sunPosition(noon).dec / DEG > 90;

        // The window is found on the night's own samples, the ones the instant above came
        // from, so the title's time is always inside the window the tile shows. The whole
        // observing day is what answers "daylight only": a planet that clears the threshold
        // while the Sun is up and never once inside the night.
        const best = polarDay ? null : bestWindow(nightTimes, curve.alt, s, night, A);
        const dayTimes = N.samples(noon, noon + DAY_MS, s.interval);
        const dayCurve = E.P.altAzCurve(b, dayTimes, s.lat, s.lon);
        const daylightOnly = polarDay
            || (!best && dayCurve.alt.some(v => v >= s.threshold));

        const muted = maxAlt < s.threshold;
        return {
            body: b,
            date: date,
            instantUtc: instantUtc,
            hm: A.formatHM(instantUtc, s.tz),
            maxAlt: maxAlt,
            threshold: s.threshold,
            muted: muted,
            mutedReason: !muted ? null : maxAlt < 0 ? 'notUp' : 'altitude',
            phaseAngleDeg: phaseAngleDeg,
            illuminated: finite(det.illuminated, b + ' illumination'),
            mag: finite(det.mag, b + ' magnitude'),
            diameterArcsec: finite(det.diameterArcsec, b + ' apparent diameter'),
            elongationDeg: finite(det.elongationDeg, b + ' elongation'),
            elongationSide: det.elongationDeg >= 0 ? 'E' : 'W',
            polePA: ang.polePA,
            sunPA: ang.sunPA,
            scale: scaleOf(det.diameterArcsec),
            best: best,
            daylightOnly: daylightOnly,
        };
    }

    // ------------------------------------------------------------------ diagram

    function viewOf(mode) {
        if (VIEWS.indexOf(mode) < 0) throw new Error('nova_solar_inner: unknown view ' + JSON.stringify(mode));
        // From the planet's own frame (x right along the equator, y up at the pole) to SVG
        // (y down): 'normal' is that view, 'diagonal' mirrors it left and right (a star
        // diagonal), 'inverted' turns it the other 180 degrees (a Newtonian). The y is what
        // carries the handedness: negating it is the whole of "north is up", the same one
        // line as in nova_solar_moon.js and nova_solar_mars.js.
        const sx = mode === 'normal' ? 1 : -1, sy = mode === 'inverted' ? 1 : -1;
        return (x, y) => ({ x: sx * x, y: sy * y });
    }

    const r6 = x => Math.round(x * 1e6) / 1e6;

    /** The lit part of a unit disc, the Moon card's construction exactly (litPath in
     *  nova_solar_moon.js): u the unit vector towards the bright limb (SVG coordinates),
     *  k the cosine of the phase angle (1 full, -1 new). The limb half on u's side, closed
     *  by the terminator: a half ellipse at -k along u. The card draws the *dark* part over
     *  the disc, so diagram() calls this with u and k both negated — which is also what
     *  carries a phase past 90 degrees, where k goes negative. */
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

    function diagram(t, mode) {
        if (!t) throw new Error('nova_solar_inner: no tonight() result to draw');
        const view = viewOf(mode);
        const pa = finite(t.polePA, 'polePA') * DEG;
        // The figure is drawn in the planet's own frame, so the pole is up whatever
        // polePA. A sky direction at position angle th (north through east) is at
        // th - polePA from that pole: (-sin, cos) of it, east being on the left.
        const fromSky = th => ({ x: -sin(th - pa), y: cos(th - pa) });
        const sun = fromSky(finite(t.sunPA, 'sunPA') * DEG);
        const u = view(sun.x, sun.y);
        const k = cos(finite(t.phaseAngleDeg, 'phase angle') * DEG);
        const path = litPath(-u.x, -u.y, -k);
        const pole_ = view(0, 1);
        const n = fromSky(0);
        const north = view(n.x, n.y);
        return {
            shaded: path.lit,
            terminator: path.terminator,
            pole: { x: r6(pole_.x), y: r6(pole_.y) },
            north: { x: r6(north.x), y: r6(north.y) },
        };
    }

    return {
        BODIES: BODIES, VIEWS: VIEWS,
        DIAM_MIN_ARCSEC: DIAM_MIN_ARCSEC, DIAM_MAX_ARCSEC: DIAM_MAX_ARCSEC,
        RADIUS_MIN_PX: RADIUS_MIN_PX, RADIUS_MAX_PX: RADIUS_MAX_PX,
        POLES: POLES,
        tonight: tonight, diagram: diagram,
        // The position angles and the pole, for the card's tests: not a stable API.
        _angles: angles, _pole: pole, _positionAngle: positionAngle,
        _positionAngleOfDate: positionAngleOfDate, _scaleOf: scaleOf,
    };
});
