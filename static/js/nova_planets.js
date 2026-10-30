/*
 * nova_planets.js — the planets and the Moon for planning: position, altitude and
 * azimuth, brightness and size, and every rise, set and transit in a time window.
 *
 * Accuracy is planning grade, about an arcminute, checked against ephem with
 * tools/test_planets.cjs (fixture tests/fixtures/planets_reference.json, built by
 * tools/gen_planets_fixture.py). Any change to this file must keep that check green.
 *
 * Theory
 *   - Mercury, Venus, the Earth-Moon barycentre, Mars, Uranus and Neptune: the JPL
 *     Keplerian elements for 1800-2050 (E. M. Standish, "Keplerian Elements for
 *     Approximate Positions of the Major Planets", table 1), J2000 ecliptic.
 *   - Jupiter and Saturn: Paul Schlyter's elements ("How to compute planetary
 *     positions") with his main mutual perturbations, mean ecliptic of date, turned to
 *     J2000 with the IAU 1976 precession. Standish's 1800-2050 fit already contains most
 *     of the great inequality, so his terms cannot be added to Standish's elements.
 *   - The Earth is the barycentre minus the geocentric Moon / 82.3 (NovaAstro's Moon).
 *   - Light time: three iterations; the planet is taken at t - tau, the Earth at t.
 *   - The result is an astrometric J2000 place, which goes through NovaAstro.frameFor()
 *     and apparent() (annual aberration, precession, nutation) like a fixed star, then
 *     diurnal parallax for the observer. The Moon is NovaAstro's own Moon throughout.
 *   - Times are UTC, used as TT and UT1 alike, as in nova_astro.js.
 *
 * Brightness and size follow ephem (libastro plans.c and circum.c), so the numbers agree
 * with Nova's Python side: the Explanatory Supplement (1992) magnitudes, in which the
 * phase angle is taken with the Sun-Earth distance fixed at 1 AU, Saturn's ring term
 * (-2.60 + 1.25 |sin B|) |sin B|, the same equatorial diameters at 1 AU, and Lane &
 * Irvine's lunar phase curve for the Moon. The Moon's illuminated fraction is
 * NovaAstro.moonPhaseAt()'s formula, unrounded. details() is geocentric.
 *
 * Conventions as in nova_astro.js: UTC milliseconds, RA in hours, everything else in
 * degrees, azimuth from north through east, no refraction. Rise and set are the body's
 * centre at -34' (ephem's horizon='-0:34' with pressure 0); the Moon topocentric.
 *
 * Depends on the global NovaAstro (src/nova_astro.js), loaded as a plain script first.
 * No DOM, no network: the module runs unchanged in Node.
 *
 * BODIES                              'mercury' ... 'neptune', 'moon'
 * position(body, utcMs)               { raHours, decDeg, distAU, sunDistAU }: geocentric
 *                                     astrometric J2000, light-time corrected; distances
 *                                     from the Earth and from the Sun.
 * altAz(body, utcMs, lat, lon)        { alt, az } for the observer.
 * altAzCurve(body, times, lat, lon)   { alt: Float64Array, az: Float64Array }, the body
 *                                     recomputed at every sample.
 * details(body, utcMs)                { mag, diameterArcsec, illuminated (0..1),
 *                                     phaseAngleDeg, elongationDeg (+ east of the Sun,
 *                                     evening; - west, morning), ringTiltDeg (Saturn only:
 *                                     the Earth's latitude over the ring plane) }
 * events(body, startUtcMs, endUtcMs, lat, lon)
 *                                     { events: [{ kind: 'rise'|'set'|'transit', utcMs,
 *                                     alt (transits only) }] in time order, upAtStart,
 *                                     alwaysUp, neverUp }. Transits are upper transits and
 *                                     are listed below the horizon too. alwaysUp/neverUp:
 *                                     no rise or set in the window and above/below the
 *                                     horizon throughout it.
 * HORIZON_ALT                         -34', the rise/set altitude of the centre.
 *
 * An unknown body name is an error: it throws.
 */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.NovaPlanets = factory();
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const PI = Math.PI, TAU = 2 * Math.PI;
    const DEG = PI / 180, HOUR = PI / 12, ARCSEC = DEG / 3600;
    const DAY_MS = 86400000;
    const AU_KM = 149597870.7;
    const LIGHT_DAYS_PER_AU = 0.0057755183;
    const EARTH_MOON_MASS = 81.3005682;      // Earth / Moon
    const EPS_J2000 = 23.4392911 * DEG;
    const HORIZON_ALT = -34 / 60;

    const BODIES = Object.freeze(['mercury', 'venus', 'mars', 'jupiter', 'saturn', 'uranus', 'neptune', 'moon']);

    const sin = Math.sin, cos = Math.cos;
    const mod = (a, n) => ((a % n) + n) % n;
    const clamp1 = x => (x > 1 ? 1 : x < -1 ? -1 : x);
    const centuries = utcMs => (utcMs / DAY_MS + 2440587.5 - 2451545.0) / 36525;

    // ------------------------------------------------------------------ elements

    // Standish table 1: a (AU), e, I, L, long. perihelion, long. node (degrees), then the
    // same six rates per Julian century.
    const STANDISH = {
        mercury: [0.38709927, 0.20563593, 7.00497902, 252.25032350, 77.45779628, 48.33076593,
            0.00000037, 0.00001906, -0.00594749, 149472.67411175, 0.16047689, -0.12534081],
        venus: [0.72333566, 0.00677672, 3.39467605, 181.97909950, 131.60246718, 76.67984255,
            0.00000390, -0.00004107, -0.00078890, 58517.81538729, 0.00268329, -0.27769418],
        emb: [1.00000261, 0.01671123, -0.00001531, 100.46457166, 102.93768193, 0.0,
            0.00000562, -0.00004392, -0.01294668, 35999.37244981, 0.32327364, 0.0],
        mars: [1.52371034, 0.09339410, 1.84969142, -4.55343205, -23.94362959, 49.55953891,
            0.00001847, 0.00007882, -0.00813131, 19140.30268499, 0.44441088, -0.29257343],
        uranus: [19.18916464, 0.04725744, 0.77263783, 313.23810451, 170.95427630, 74.01692503,
            -0.00196176, -0.00004397, -0.00242939, 428.48202785, 0.40805281, 0.04240589],
        neptune: [30.06992276, 0.00859048, 1.77004347, -55.12002969, 44.96476227, 131.78422574,
            0.00026291, 0.00005105, 0.00035372, 218.45945325, -0.32241464, -0.00508664],
    };

    // Schlyter: node N, inclination i, argument of perihelion w, a, e, mean anomaly M,
    // each as [value at d = 0, rate per day], d counted from 2000 Jan 0.0.
    const SCHLYTER = {
        jupiter: [100.4542, 2.76854e-5, 1.3030, -1.557e-7, 273.8777, 1.64505e-5,
            5.20256, 0, 0.048498, 4.469e-9, 19.8950, 0.0830853001],
        saturn: [113.6634, 2.38980e-5, 2.4886, -1.081e-7, 339.3939, 2.97661e-5,
            9.55475, 0, 0.055546, -9.499e-9, 316.9670, 0.0334442282],
    };

    // ephem's vis_elements: diameter at 1 AU (arcsec), magnitude at 1 AU and phase 0,
    // then A, B, C of A x + B x^2 + C x^3 with x = phase angle in degrees / 100.
    const VIS = {
        mercury: [6.74, -0.36, 3.8, -2.73, 2.00],
        venus: [16.92, -4.29, 0.09, 2.39, -0.65],
        mars: [9.36, -1.52, 1.60, 0, 0],
        jupiter: [196.74, -9.25, 0.50, 0, 0],
        saturn: [165.6, -8.88, 4.40, 0, 0],
        uranus: [70.481, -7.19, 0.28, 0, 0],
        neptune: [68.294, -6.87, 0, 0, 0],
    };
    const MOON_RADIUS_KM = 1740;                          // libastro MRAD
    const MOON_MEAN_DIST_AU = 60.2665 * 6378.16 / 149597870; // libastro mean_em_dist

    // NovaAstro is a global from the script loaded before this one; looked up at call
    // time, as nova_transit.js does it.
    function engine() {
        const A = typeof NovaAstro !== 'undefined' ? NovaAstro : null;
        if (!A) throw new Error('nova_planets: NovaAstro is not loaded (src/nova_astro.js must come first)');
        return A;
    }

    function checkBody(body) {
        if (BODIES.indexOf(body) < 0) throw new Error('nova_planets: unknown body ' + JSON.stringify(body));
    }

    // ------------------------------------------------------------------ vectors

    const norm = v => Math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);

    function eclipticToEquatorial(v, eps) {
        const ce = cos(eps), se = sin(eps);
        return [v[0], v[1] * ce - v[2] * se, v[1] * se + v[2] * ce];
    }

    /** Heliocentric ecliptic vector of an orbit; angles in radians, w = argument of perihelion. */
    function orbitVector(a, e, inc, w, node, M) {
        M = mod(M + PI, TAU) - PI;
        let E = M + e * sin(M);
        for (let k = 0; k < 20; k++) {
            const dE = (E - e * sin(E) - M) / (1 - e * cos(E));
            E -= dE;
            if (Math.abs(dE) < 1e-12) break;
        }
        const xp = a * (cos(E) - e), yp = a * Math.sqrt(1 - e * e) * sin(E);
        const cw = cos(w), sw = sin(w), cO = cos(node), sO = sin(node), ci = cos(inc), si = sin(inc);
        return [
            (cw * cO - sw * sO * ci) * xp + (-sw * cO - cw * sO * ci) * yp,
            (cw * sO + sw * cO * ci) * xp + (-sw * sO + cw * cO * ci) * yp,
            sw * si * xp + cw * si * yp,
        ];
    }

    /** IAU 1976 precession, J2000 -> mean equator of date, the same matrix as NovaAstro.frameFor(). */
    function precession(T) {
        const zeta = (2306.2181 * T + 0.30188 * T * T + 0.017998 * T * T * T) * ARCSEC;
        const z = (2306.2181 * T + 1.09468 * T * T + 0.018203 * T * T * T) * ARCSEC;
        const th = (2004.3109 * T - 0.42665 * T * T - 0.041833 * T * T * T) * ARCSEC;
        const cz = cos(zeta), sz = sin(zeta), cZ = cos(z), sZ = sin(z), ct = cos(th), st = sin(th);
        return [
            cZ * ct * cz - sZ * sz, -cZ * ct * sz - sZ * cz, -cZ * st,
            sZ * ct * cz + cZ * sz, -sZ * ct * sz + cZ * cz, -sZ * st,
            st * cz, -st * sz, ct,
        ];
    }

    /** Transpose of a row-major 3x3 matrix applied to v. */
    function applyTransposed(M, v) {
        return [
            M[0] * v[0] + M[3] * v[1] + M[6] * v[2],
            M[1] * v[0] + M[4] * v[1] + M[7] * v[2],
            M[2] * v[0] + M[5] * v[1] + M[8] * v[2],
        ];
    }

    function standish(name, T) {
        const k = STANDISH[name];
        const a = k[0] + k[6] * T, e = k[1] + k[7] * T, inc = (k[2] + k[8] * T) * DEG;
        const L = k[3] + k[9] * T, peri = k[4] + k[10] * T, node = k[5] + k[11] * T;
        const v = orbitVector(a, e, inc, (peri - node) * DEG, node * DEG, (L - peri) * DEG);
        return eclipticToEquatorial(v, EPS_J2000);
    }

    function schlyter(name, utcMs) {
        const d = utcMs / DAY_MS + 2440587.5 - 2451543.5, T = centuries(utcMs);
        const at = i => SCHLYTER[name][i] + SCHLYTER[name][i + 1] * d;
        const v = orbitVector(at(6), at(8), at(2) * DEG, at(4) * DEG, at(0) * DEG, at(10) * DEG);

        const Mj = (SCHLYTER.jupiter[10] + SCHLYTER.jupiter[11] * d) * DEG;
        const Ms = (SCHLYTER.saturn[10] + SCHLYTER.saturn[11] * d) * DEG;
        let dl, db = 0;
        if (name === 'jupiter') {
            dl = -0.332 * sin(2 * Mj - 5 * Ms - 67.6 * DEG) - 0.056 * sin(2 * Mj - 2 * Ms + 21 * DEG)
                + 0.042 * sin(3 * Mj - 5 * Ms + 21 * DEG) - 0.036 * sin(Mj - 2 * Ms)
                + 0.022 * cos(Mj - Ms) + 0.023 * sin(2 * Mj - 3 * Ms + 52 * DEG)
                - 0.016 * sin(Mj - 5 * Ms - 69 * DEG);
        } else {
            dl = 0.812 * sin(2 * Mj - 5 * Ms - 67.6 * DEG) - 0.229 * cos(2 * Mj - 4 * Ms - 2 * DEG)
                + 0.119 * sin(Mj - 2 * Ms - 3 * DEG) + 0.046 * sin(2 * Mj - 6 * Ms - 69 * DEG)
                + 0.014 * sin(Mj - 3 * Ms + 32 * DEG);
            db = -0.020 * cos(2 * Mj - 4 * Ms - 2 * DEG) + 0.018 * sin(2 * Mj - 6 * Ms - 49 * DEG);
        }
        const r = norm(v);
        const lon = Math.atan2(v[1], v[0]) + dl * DEG, lat = Math.asin(v[2] / r) + db * DEG;
        const ecl = [r * cos(lat) * cos(lon), r * cos(lat) * sin(lon), r * sin(lat)];

        // Mean ecliptic of date -> mean equator of date -> J2000
        const eps0 = (23.4392911 - 0.0130042 * T - 1.64e-7 * T * T + 5.04e-7 * T * T * T) * DEG;
        return applyTransposed(precession(T), eclipticToEquatorial(ecl, eps0));
    }

    /** Heliocentric J2000 equatorial position (AU) of a planet or of the barycentre 'emb'. */
    function heliocentric(name, utcMs) {
        if (name === 'jupiter' || name === 'saturn') return schlyter(name, utcMs);
        return standish(name, centuries(utcMs));
    }

    /** Geocentric Moon as a vector in AU, true equator of date. */
    function moonVector(A, utcMs) {
        const m = A.moonGeocentric(utcMs), r = m.dist / AU_KM, cd = cos(m.dec);
        return [r * cd * cos(m.ra), r * cd * sin(m.ra), r * sin(m.dec)];
    }

    /**
     * Heliocentric Earth, J2000 equatorial (AU). The Moon's offset is at most 3e-5 AU, so
     * its frame of date stands in for J2000 here (under 1e-7 AU of difference).
     */
    function earth(A, utcMs) {
        const b = heliocentric('emb', utcMs), m = moonVector(A, utcMs), k = 1 + EARTH_MOON_MASS;
        return [b[0] - m[0] / k, b[1] - m[1] / k, b[2] - m[2] / k];
    }

    /** Geocentric light-time-corrected planet: { geo (J2000 vector), dist, helio, sunDist, earth }. */
    function planetState(A, body, utcMs) {
        const E = earth(A, utcMs);
        let tau = 0, P, D, dist;
        for (let k = 0; k < 3; k++) {
            P = heliocentric(body, utcMs - tau * DAY_MS);
            D = [P[0] - E[0], P[1] - E[1], P[2] - E[2]];
            dist = norm(D);
            tau = dist * LIGHT_DAYS_PER_AU;
        }
        return { geo: D, dist, helio: P, sunDist: norm(P), earth: E };
    }

    function toRaDec(v) {
        return {
            raHours: mod(Math.atan2(v[1], v[0]), TAU) / HOUR,
            decDeg: Math.atan2(v[2], Math.sqrt(v[0] * v[0] + v[1] * v[1])) / DEG,
        };
    }

    // ------------------------------------------------------------------ public: positions

    function position(body, utcMs) {
        checkBody(body);
        const A = engine();
        if (body === 'moon') {
            const m = moonVector(A, utcMs);
            const g = applyTransposed(A.frameFor(utcMs).M, m);   // true of date -> J2000
            const E = earth(A, utcMs);
            const p = toRaDec(g);
            return {
                raHours: p.raHours, decDeg: p.decDeg, distAU: norm(g),
                sunDistAU: norm([E[0] + g[0], E[1] + g[1], E[2] + g[2]]),
            };
        }
        const s = planetState(A, body, utcMs);
        const p = toRaDec(s.geo);
        return { raHours: p.raHours, decDeg: p.decDeg, distAU: s.dist, sunDistAU: s.sunDist };
    }

    function horizontal(ra, dec, lst, latRad) {
        const H = lst - ra;
        const sd = sin(dec), cd = cos(dec), sl = sin(latRad), cl = cos(latRad), cH = cos(H);
        const alt = Math.asin(clamp1(sl * sd + cl * cd * cH));
        const az = Math.atan2(-cd * sin(H), sd * cl - cd * cH * sl);
        return { alt: alt / DEG, az: mod(az, TAU) / DEG };
    }

    function altAz(body, utcMs, lat, lon) {
        checkBody(body);
        const A = engine();
        if (body === 'moon') return A.moonAltAz(utcMs, lat, lon);

        const s = planetState(A, body, utcMs);
        const p = toRaDec(s.geo);
        const f = A.frameFor(utcMs);
        const ap = A.apparent(p.raHours, p.decDeg, f);
        const lst = A.gast(utcMs, f.nut) + lon * DEG;

        // Diurnal parallax: the observer on the WGS84 ellipsoid, as nova_astro's Moon does it
        const latRad = lat * DEG, fl = 1 / 298.257223563, a = 6378.137 / AU_KM;
        const C = 1 / Math.sqrt(cos(latRad) * cos(latRad) + (1 - fl) * (1 - fl) * sin(latRad) * sin(latRad));
        const S = (1 - fl) * (1 - fl) * C;
        const cd = cos(ap.dec);
        const x = s.dist * cd * cos(ap.ra) - a * C * cos(latRad) * cos(lst);
        const y = s.dist * cd * sin(ap.ra) - a * C * cos(latRad) * sin(lst);
        const z = s.dist * sin(ap.dec) - a * S * sin(latRad);
        return horizontal(Math.atan2(y, x), Math.atan2(z, Math.sqrt(x * x + y * y)), lst, latRad);
    }

    function altAzCurve(body, times, lat, lon) {
        checkBody(body);
        const n = times.length;
        const alt = new Float64Array(n), az = new Float64Array(n);
        for (let i = 0; i < n; i++) {
            const h = altAz(body, times[i], lat, lon);
            alt[i] = h.alt;
            az[i] = h.az;
        }
        return { alt, az };
    }

    // ------------------------------------------------------------------ public: details

    const eclipticLongitude = (v, eps) => Math.atan2(v[1] * cos(eps) + v[2] * sin(eps), v[0]);
    const signedElongation = (psi, lonBody, lonSun) => (mod(lonBody - lonSun, TAU) < PI ? psi : -psi);

    function moonDetails(A, utcMs) {
        // moonPhaseAt()'s geometry, unrounded
        const m = A.moonGeocentric(utcMs), s = A.sunPosition(utcMs);
        const psi = Math.acos(clamp1(sin(s.dec) * sin(m.dec) + cos(s.dec) * cos(m.dec) * cos(s.ra - m.ra)));
        const R = s.dist * AU_KM;
        const i = Math.atan2(R * sin(psi), m.dist - R * cos(psi));

        const cd = cos(m.dec);
        const lonMoon = eclipticLongitude([cd * cos(m.ra), cd * sin(m.ra), sin(m.dec)], s.nut.eps);

        // Lane & Irvine (1973) V-band phase curve, as ephem 4.2 has it
        const pang = i / DEG;
        let mag;
        if (pang <= 40) {
            mag = -12.72 + 0.0267 * (pang - 20) + 0.534;
        } else {
            const p = pang - 80;
            mag = -12.72 + p * (0.03188 + p * (1.9621e-4 + p * 1.7256e-6)) + 2.14;
        }
        const dAU = m.dist / AU_KM;
        const sunDist = Math.sqrt(dAU * dAU + s.dist * s.dist - 2 * dAU * s.dist * cos(psi));
        mag += 5 * Math.log10(dAU / MOON_MEAN_DIST_AU) + 5 * Math.log10(sunDist);

        return {
            mag,
            diameterArcsec: 2 * Math.asin(MOON_RADIUS_KM / m.dist) / ARCSEC,
            illuminated: (1 + cos(i)) / 2,
            phaseAngleDeg: pang,
            elongationDeg: signedElongation(psi, lonMoon, s.lon) / DEG,
        };
    }

    function details(body, utcMs) {
        checkBody(body);
        const A = engine();
        if (body === 'moon') return moonDetails(A, utcMs);

        const st = planetState(A, body, utcMs);
        const r = st.sunDist, D = st.dist, R = norm(st.earth);
        const vis = VIS[body];

        const cosI = clamp1((r * r + D * D - R * R) / (2 * r * D));
        // ephem's magnitude takes the phase angle with the Sun-Earth distance set to 1 AU
        const x = Math.acos(clamp1((r * r + D * D - 1) / (2 * r * D))) / DEG / 100;
        let mag = vis[1] + 5 * Math.log10(r * D) + x * (vis[2] + x * (vis[3] + x * vis[4]));

        const u = [st.geo[0] / D, st.geo[1] / D, st.geo[2] / D];
        const sun = [-st.earth[0] / R, -st.earth[1] / R, -st.earth[2] / R];
        const psi = Math.acos(clamp1(u[0] * sun[0] + u[1] * sun[1] + u[2] * sun[2]));

        const out = {
            mag: 0,
            diameterArcsec: vis[0] / D,
            illuminated: (1 + cosI) / 2,
            phaseAngleDeg: Math.acos(cosI) / DEG,
            elongationDeg: signedElongation(psi, eclipticLongitude(u, EPS_J2000), eclipticLongitude(sun, EPS_J2000)) / DEG,
        };

        if (body === 'saturn') {
            // IAU pole of Saturn (J2000); B is the Earth's latitude over the ring plane
            const T = centuries(utcMs);
            const pa = (40.589 - 0.036 * T) * DEG, pd = (83.537 - 0.004 * T) * DEG;
            const pole = [cos(pd) * cos(pa), cos(pd) * sin(pa), sin(pd)];
            const sinB = clamp1(-(pole[0] * u[0] + pole[1] * u[1] + pole[2] * u[2]));
            const sb = Math.abs(sinB);
            mag += (-2.60 + 1.25 * sb) * sb;
            out.ringTiltDeg = Math.asin(sinB) / DEG;
        }
        out.mag = mag;
        return out;
    }

    // ------------------------------------------------------------------ public: events

    const STEP_MS = 10 * 60000;
    const GRAZE_DEG = 0.5;

    function events(body, startUtcMs, endUtcMs, lat, lon) {
        checkBody(body);
        const out = { events: [], upAtStart: false, alwaysUp: false, neverUp: false };
        if (!(endUtcMs > startUtcMs)) return out;

        // Altitude above the rise/set horizon, and the hour angle (radians, -pi..pi)
        // recovered from altitude and azimuth, so the Moon and planets share one path.
        const latRad = lat * DEG, sl = sin(latRad), cl = cos(latRad);
        const sample = t => {
            const h = altAz(body, t, lat, lon);
            const ca = cos(h.alt * DEG);
            const north = ca * cos(h.az * DEG), east = ca * sin(h.az * DEG), up = sin(h.alt * DEG);
            return { t, a: h.alt - HORIZON_ALT, alt: h.alt, H: Math.atan2(-east, up * cl - north * sl) };
        };
        const bisect = (lo, hi, f) => {
            // f(lo.t) and f(hi.t) differ in sign; narrow to a second
            while (hi.t - lo.t > 1000) {
                const mid = sample((lo.t + hi.t) / 2);
                if ((f(mid) >= 0) === (f(lo) >= 0)) lo = mid; else hi = mid;
            }
            return Math.abs(f(lo)) <= Math.abs(f(hi)) ? lo : hi;
        };
        const fa = s => s.a, fH = s => s.H;

        const pts = [];
        for (let t = startUtcMs; ; t += STEP_MS) {
            pts.push(sample(Math.min(t, endUtcMs)));
            if (t >= endUtcMs) break;
        }

        const found = [];
        const crossing = (lo, hi) => {
            const p = bisect(lo, hi, fa);
            found.push({ kind: lo.a < 0 ? 'rise' : 'set', utcMs: Math.round(p.t) });
        };

        for (let k = 0; k + 1 < pts.length; k++) {
            const lo = pts[k], hi = pts[k + 1];
            if ((lo.a < 0) !== (hi.a < 0)) crossing(lo, hi);

            // Upper transit: hour angle passes 0 upwards (not the wrap at +-pi)
            if (lo.H < 0 && hi.H >= 0 && hi.H - lo.H < PI) {
                const p = bisect(lo, hi, fH);
                found.push({ kind: 'transit', utcMs: Math.round(p.t), alt: p.alt });
            }
        }

        // A grazing pass can rise and set between two samples: look at every extremum
        // near the horizon that the samples did not see cross it.
        for (let k = 1; k + 1 < pts.length; k++) {
            const p0 = pts[k - 1], p1 = pts[k], p2 = pts[k + 1];
            const isMax = p1.a >= p0.a && p1.a >= p2.a, isMin = p1.a <= p0.a && p1.a <= p2.a;
            if (!(isMax || isMin) || Math.abs(p1.a) > GRAZE_DEG) continue;
            if ((p0.a < 0) !== (p1.a < 0) || (p1.a < 0) !== (p2.a < 0)) continue;
            const sgn = isMax ? 1 : -1;
            let lo = p0.t, hi = p2.t;
            for (let n = 0; n < 40 && hi - lo > 1000; n++) {    // golden section
                const m1 = hi - (hi - lo) * 0.618034, m2 = lo + (hi - lo) * 0.618034;
                if (sgn * sample(m1).a > sgn * sample(m2).a) hi = m2; else lo = m1;
            }
            const ext = sample((lo + hi) / 2);
            if ((ext.a < 0) !== (p1.a < 0)) {
                crossing(p0, ext);
                crossing(ext, p2);
            }
        }

        found.sort((x, y) => x.utcMs - y.utcMs);
        out.events = found;
        out.upAtStart = pts[0].a >= 0;
        if (!found.some(e => e.kind !== 'transit')) {
            out.alwaysUp = out.upAtStart;
            out.neverUp = !out.upAtStart;
        }
        return out;
    }

    return {
        BODIES, HORIZON_ALT,
        position, altAz, altAzCurve, details, events,
    };
});
