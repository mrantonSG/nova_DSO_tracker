/*
 * nova_moon.js — the Moon as a globe: libration, the Sun's place over the lunar surface
 * (colongitude, subsolar latitude), the position angles of the axis and of the bright
 * limb, and the Sun's altitude over any point of the surface.
 *
 * Maths only: which features to show, and when, is the page's business.
 *
 * Accuracy is checked with tools/test_moon_libration.cjs (fixture
 * tests/fixtures/moon_libration_reference.json, built by
 * tools/gen_moon_libration_fixture.py) against the IAU rotation model of the Moon
 * (Archinal et al. 2011, the pole and prime meridian with their E1..E13 terms) applied
 * to ephem's positions, and against ephem's own libration. Any change to this file must
 * keep that check green. Expected: libration, colongitude, subsolar latitude, axis angle
 * and the Sun's altitude over a feature to a few hundredths of a degree.
 *
 * ephem's colongitude is NOT a reference: libastro's mooncolong.c (Bruning and Talcott,
 * 1995) takes cos() of the Moon's latitude in degrees as if it were radians, which puts
 * its colongitude up to 0.36 degrees off near the quarters.
 *
 * Theory
 *   - The Moon and the Sun are NovaAstro's: moonGeocentric() (Meeus ch. 47, apparent,
 *     true equator of date), its ecliptic longitude and latitude recovered by turning
 *     RA and Dec back with the true obliquity, and sunPosition().
 *   - Libration: Meeus, Astronomical Algorithms, 2nd ed., ch. 53. Optical libration
 *     (53.1) plus the physical libration series (rho, sigma, tau). The Sun's
 *     selenographic position is the same computation for the heliocentric direction of
 *     the Moon (lambda_H, beta_H), physical libration included.
 *   - Geocentric throughout: the libration seen from the Earth's centre, as ephem gives
 *     it. The observer's own (diurnal) libration, up to about 1 degree, is left out.
 *   - Times are UTC, used as TT, as in nova_astro.js (about 0.01 degree of colongitude).
 *
 * Conventions
 *   - Degrees everywhere. Selenographic longitude east-positive (IAU: Mare Crisium is
 *     at +59), -180..180; latitude north-positive.
 *   - libration.lon > 0: the sub-Earth point lies at positive longitude, so the limb on
 *     the Mare Crisium side is turned towards us; libration.lat > 0: the north limb.
 *   - Colongitude 0..360: the selenographic longitude of the morning terminator,
 *     counted westward (Meeus c0 = 90 - l0). It grows about 12.19 degrees a day; about
 *     0 at first quarter, 90 at full, 180 at last quarter, 270 at new Moon.
 *   - Position angles from celestial north through east, 0..360.
 *
 * geometry(utcMs) -> {
 *     libration: { lon, lat },           total (optical + physical) libration
 *     optical:   { lon, lat },           optical part alone
 *     colongitude,                       0..360
 *     subsolar:  { lon, lat },           the point with the Sun overhead
 *     axisPA,                            position angle of the Moon's north pole (P)
 *     brightLimbPA,                      position angle of the bright limb's midpoint (chi)
 *     distanceKm                         geocentric
 *   }
 * sunAltitude(g, lat, lon)    degrees: the Sun's altitude over that point, for its centre
 *                             seen from the Moon's centre (Meeus 53, sin h = sin b0 sin
 *                             lat + cos b0 cos lat sin(c0 + lon)).
 *                             Negative on the night side.
 * isMorning(g, lat, lon)      true while the Sun is rising there (the morning,
 *                             waxing side of the terminator), false while it is setting.
 * fromCentre(g, lat, lon)     degrees between that point and the sub-Earth point: under
 *                             90 on the Earth-facing side; 90 on the limb.
 * project(g, lat, lon)        { x, y, z }: the point on a unit disc as seen from the
 *                             Earth's centre, lunar north up (+y), the Mare Crisium side
 *                             to the right (+x), z towards the Earth (z < 0: far side).
 *                             Not turned by axisPA: the page draws the Moon north up.
 * tilt(g)                     { amount, direction }: how far the sub-Earth point is from
 *                             the mean centre (degrees), and the direction of the limb
 *                             turned towards us, on the disc of project(): 0 north, 90
 *                             the Mare Crisium side (+x), 180 south, 270 the other side.
 *
 * Depends on the global NovaAstro (src/nova_astro.js), loaded as a plain script first.
 * No DOM, no network: the module runs unchanged in Node.
 */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.NovaMoon = factory();
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const PI = Math.PI, TAU = 2 * Math.PI;
    const DEG = PI / 180;
    const DAY_MS = 86400000;
    const AU_KM = 149597870.7;
    const I = 1.54242 * DEG;                 // inclination of the lunar equator to the ecliptic

    const sin = Math.sin, cos = Math.cos;
    const mod = (a, n) => ((a % n) + n) % n;
    const plusMinus180 = d => mod(d + 180, 360) - 180;
    const clamp1 = x => (x > 1 ? 1 : x < -1 ? -1 : x);
    const centuries = utcMs => (utcMs / DAY_MS + 2440587.5 - 2451545.0) / 36525;

    // NovaAstro is a global from the script loaded before this one; looked up at call
    // time, as nova_planets.js does it.
    function engine() {
        const A = typeof NovaAstro !== 'undefined' ? NovaAstro : null;
        if (!A) throw new Error('nova_moon: NovaAstro is not loaded (src/nova_astro.js must come first)');
        return A;
    }

    // ------------------------------------------------------------------ arguments

    /** Meeus 47.2-47.7 and 47.6: the fundamental arguments (radians) and E. */
    function fundamentals(T) {
        const T2 = T * T, T3 = T2 * T, T4 = T3 * T;
        return {
            D: (297.8501921 + 445267.1114034 * T - 0.0018819 * T2 + T3 / 545868 - T4 / 113065000) * DEG,
            M: (357.5291092 + 35999.0502909 * T - 0.0001536 * T2 + T3 / 24490000) * DEG,
            Mp: (134.9633964 + 477198.8675055 * T + 0.0087414 * T2 + T3 / 69699 - T4 / 14712000) * DEG,
            F: (93.2720950 + 483202.0175233 * T - 0.0036539 * T2 - T3 / 3526000 + T4 / 863310000) * DEG,
            Om: (125.0445479 - 1934.1362891 * T + 0.0020754 * T2 + T3 / 467441 - T4 / 60616000) * DEG,
            E: 1 - 0.002516 * T - 0.0000074 * T2,
            K1: (119.75 + 131.849 * T) * DEG,
            K2: (72.56 + 20.186 * T) * DEG,
        };
    }

    /** Meeus ch. 53: the physical libration quantities rho, sigma, tau (radians). */
    function physical(a) {
        const D = a.D, M = a.M, Mp = a.Mp, F = a.F;
        const rho = -0.02752 * cos(Mp) - 0.02245 * sin(F) + 0.00684 * cos(Mp - 2 * F)
            - 0.00293 * cos(2 * F) - 0.00085 * cos(2 * F - 2 * D) - 0.00054 * cos(Mp - 2 * D)
            - 0.00020 * sin(Mp + F) - 0.00020 * cos(Mp + 2 * F) - 0.00020 * cos(Mp - F)
            + 0.00014 * cos(Mp + 2 * F - 2 * D);
        const sigma = -0.02816 * sin(Mp) + 0.02244 * cos(F) - 0.00682 * sin(Mp - 2 * F)
            - 0.00279 * sin(2 * F) - 0.00083 * sin(2 * F - 2 * D) + 0.00069 * sin(Mp - 2 * D)
            + 0.00040 * cos(Mp + F) - 0.00025 * sin(2 * Mp) - 0.00023 * sin(Mp + 2 * F)
            + 0.00020 * cos(Mp - F) + 0.00019 * sin(Mp - F) + 0.00013 * sin(Mp + 2 * F - 2 * D)
            - 0.00010 * cos(Mp - 3 * F);
        const tau = 0.02520 * a.E * sin(M) + 0.00473 * sin(2 * Mp - 2 * F) - 0.00467 * sin(Mp)
            + 0.00396 * sin(a.K1) + 0.00276 * sin(2 * Mp - 2 * D) + 0.00196 * sin(a.Om)
            - 0.00183 * cos(Mp - F) + 0.00115 * sin(Mp - 2 * D) - 0.00096 * sin(Mp - D)
            + 0.00046 * sin(2 * F - 2 * D) - 0.00039 * sin(Mp - F) - 0.00032 * sin(Mp - M - D)
            + 0.00027 * sin(2 * Mp - M - 2 * D) + 0.00023 * sin(a.K2) - 0.00014 * sin(2 * D)
            + 0.00014 * cos(2 * Mp - 2 * F) - 0.00012 * sin(Mp - 2 * F) - 0.00012 * sin(2 * Mp)
            + 0.00011 * sin(2 * Mp - 2 * M - 2 * D);
        return { rho: rho * DEG, sigma: sigma * DEG, tau: tau * DEG };
    }

    /**
     * Meeus 53.1 plus the physical terms, for a direction of ecliptic longitude lam and
     * latitude bet (radians, apparent, of date) seen from the Moon's centre outward.
     * Returns the selenographic { l, b } of that direction (radians, total), the optical
     * part, and A (needed for the axis angle).
     */
    function selenographic(lam, bet, a, dpsi, ph) {
        const W = lam - dpsi - a.Om;
        const A = Math.atan2(sin(W) * cos(bet) * cos(I) - sin(bet) * sin(I), cos(W) * cos(bet));
        const l1 = A - a.F;
        const b1 = Math.asin(clamp1(-sin(W) * cos(bet) * sin(I) - sin(bet) * cos(I)));
        const l2 = -ph.tau + (ph.rho * cos(A) + ph.sigma * sin(A)) * Math.tan(b1);
        const b2 = ph.sigma * cos(A) - ph.rho * sin(A);
        return { l: l1 + l2, b: b1 + b2, l1, b1 };
    }

    // ------------------------------------------------------------------ public

    function geometry(utcMs) {
        const Astro = engine();
        const T = centuries(utcMs);
        const m = Astro.moonGeocentric(utcMs), s = Astro.sunPosition(utcMs);
        const n = m.nut, eps = n.eps;

        // The Moon's apparent ecliptic place: moonGeocentric's RA/Dec turned back with
        // the obliquity it was turned with
        const lam = Math.atan2(sin(m.ra) * cos(eps) + Math.tan(m.dec) * sin(eps), cos(m.ra));
        const bet = Math.asin(clamp1(sin(m.dec) * cos(eps) - cos(m.dec) * sin(eps) * sin(m.ra)));

        const a = fundamentals(T);
        const ph = physical(a);
        const earth = selenographic(lam, bet, a, n.dpsi, ph);

        // The Sun's direction from the Moon (Meeus ch. 53, "selenographic position of
        // the Sun"): heliocentric longitude and latitude of the Moon
        const ratio = m.dist / (s.dist * AU_KM);
        const lamH = s.lon + PI + ratio * cos(bet) * sin(s.lon - lam);
        const betH = ratio * bet;
        const sun = selenographic(lamH, betH, a, n.dpsi, ph);

        // Position angle of the axis
        const V = a.Om + n.dpsi + ph.sigma / sin(I);
        const X = sin(I + ph.rho) * sin(V);
        const Y = sin(I + ph.rho) * cos(V) * cos(eps) - cos(I + ph.rho) * sin(eps);
        const omega = Math.atan2(X, Y);
        const P = Math.asin(clamp1(Math.sqrt(X * X + Y * Y) * cos(m.ra - omega) / cos(earth.b)));

        // Position angle of the bright limb (Meeus 48.5)
        const chi = Math.atan2(cos(s.dec) * sin(s.ra - m.ra),
            sin(s.dec) * cos(m.dec) - cos(s.dec) * sin(m.dec) * cos(s.ra - m.ra));

        const l0 = plusMinus180(sun.l / DEG);
        return {
            libration: { lon: plusMinus180(earth.l / DEG), lat: earth.b / DEG },
            optical: { lon: plusMinus180(earth.l1 / DEG), lat: earth.b1 / DEG },
            colongitude: mod(90 - l0, 360),
            subsolar: { lon: l0, lat: sun.b / DEG },
            axisPA: mod(P / DEG, 360),
            brightLimbPA: mod(chi / DEG, 360),
            distanceKm: m.dist,
        };
    }

    function sunAltitude(g, lat, lon) {
        const b0 = g.subsolar.lat * DEG, th = lat * DEG;
        const x = sin(b0) * sin(th) + cos(b0) * cos(th) * sin((g.colongitude + lon) * DEG);
        return Math.asin(clamp1(x)) / DEG;
    }

    function isMorning(g, lat, lon) {
        return cos((g.colongitude + lon) * DEG) > 0;
    }

    function fromCentre(g, lat, lon) {
        const b = g.libration.lat * DEG, th = lat * DEG;
        const c = sin(b) * sin(th) + cos(b) * cos(th) * cos((lon - g.libration.lon) * DEG);
        return Math.acos(clamp1(c)) / DEG;
    }

    function project(g, lat, lon) {
        const b = g.libration.lat * DEG, th = lat * DEG, dl = (lon - g.libration.lon) * DEG;
        return {
            x: cos(th) * sin(dl),
            y: cos(b) * sin(th) - sin(b) * cos(th) * cos(dl),
            z: sin(b) * sin(th) + cos(b) * cos(th) * cos(dl),
        };
    }

    function tilt(g) {
        const l = g.libration.lon * DEG, b = g.libration.lat * DEG;
        return {
            amount: Math.acos(clamp1(cos(l) * cos(b))) / DEG,
            direction: mod(Math.atan2(sin(l) * cos(b), sin(b)) / DEG, 360),
        };
    }

    return { geometry, sunAltitude, isMorning, fromCentre, project, tilt };
});
