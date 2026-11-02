/*
 * nova_mars.js — Mars as a globe: the central meridian, the planetocentric latitude of
 * the Earth and of the Sun, the position angle of the axis, and how big the disc is.
 *
 * Maths only: which of it to show, and when, is the page's business.
 *
 * Accuracy is checked with tools/test_mars.cjs (fixture tests/fixtures/mars_reference.json,
 * built by tools/gen_mars_fixture.py), which applies the same IAU rotation model to ephem's
 * positions as an independent route and compares with JPL Horizons' own sub-observer and
 * sub-solar longitudes and latitudes. Any change to this file must keep that check green.
 *
 * Pass marks, measured over the fixture's 680 instants (2024 to 2040-01-14), each the worst
 * case on that fixture with the mark set just above it:
 *   central meridian   0.05 deg (0.0374)   De, Ds   0.02 deg (0.0070), 0.03 deg (0.0119)
 *   polePA             0.04 deg (0.0158)   sunPA    0.1 deg (0.0465) at phase angle
 *                                                   >= 6 deg; 0.6 deg (0.3871) over all
 *   diameter           0.02 arcsec (0.0054)     illumination  0.02 per cent (0.0061)
 *   distances          0.002 AU (0.0004, 0.0003); light time is distance / c, exactly
 *   against Horizons: central meridian 0.05 deg (0.0286), De 0.02 (0.0069), Ds 0.03 (0.0128)
 * The marks are looser than nova_jupiter.js's because Mars's own place differs from ephem's
 * by about 0.03 deg where Jupiter's differs by 0.007, and every rotation quantity here is
 * read off that place. The two sunPA marks are the conditioned and the tripwire form of one
 * quantity - the position angle of the Sun as seen from Mars loses all its conditioning
 * when the phase angle passes through zero, at conjunction and opposition. The header of
 * tools/test_mars.cjs carries the measured band table.
 *
 * Theory
 *   - Mars's place and the frame: NovaPlanets.position('mars') (astrometric J2000,
 *     light-time corrected) and NovaAstro.frameFor(), as nova_jupiter.js does it for
 *     Jupiter. The pole is the ICRF pole; the position angle is taken against Mars's place
 *     turned to the mean equator of date.
 *   - Rotation: the IAU/WGCCRE model. alpha0 = 317.68143 - 0.1061 T,
 *     delta0 = 52.88650 - 0.0609 T, W = 176.630 + 350.89198226 d (d in days TDB from J2000).
 *     These are the 2009 report's Mars values. Source: Archinal et al., "Report of the IAU
 *     Working Group on Cartographic Coordinates and Rotational Elements: 2009", Celest.
 *     Mech. Dyn. Astr. 109, 101 (2011), as republished in the SPICE kernel pck00011.tpc,
 *     whose Mars section lists exactly these three lines under "Old values: Values are from
 *     the 2009 IAU report". The kernel's "Current values" are the 2015 report's, and they
 *     are a different pole - alpha0 317.269202 - 0.10927547 T, delta0 54.432516 - 0.05827105 T,
 *     W = 176.049863 + 350.891982443297 d - so do not mix the two: 1.5 deg of de and 0.58 deg
 *     of central meridian separate them. Horizons is on the 2009 set, which is why they are
 *     the ones here, and the fixture measures that.
 *     Mars's 0 deg meridian is the one through the crater Airy-0.
 *   - The central meridian is the sub-observer longitude, not W: W is evaluated at the
 *     light-time-corrected instant and the Earth's direction in the body frame is taken off
 *     it. The two drift several degrees apart over a single apparition.
 *
 * Conventions
 *   - Degrees everywhere. cm 0..360, planetographic: positive westward, increasing with
 *     the rotation of the planet, and the same quantity JPL Horizons prints as ObsSub-LON
 *     for Mars. de and ds are planetocentric, north-positive; Horizons' ObsSub-LAT is
 *     planetodetic, so the two differ by flatness*sin(2*lat), up to 0.26 deg at Mars.
 *   - polePA: position angle of Mars's north pole, from celestial north through east.
 *   - Times are UTC. Positions use UTC as TT, as in nova_astro.js; the rotation angle W is
 *     the one place where the difference shows, so TT is applied there (TT_MINUS_UTC_DAYS).
 *
 * geometry(utcMs) -> {
 *     cm,                          sub-Earth planetocentric longitude, 0..360
 *     de, ds,                      planetocentric latitude of the Earth, of the Sun
 *     polePA,
 *     diameterArcsec,              apparent equatorial diameter
 *     distanceAU, sunDistAU,
 *     phaseAngleDeg,
 *     lightTimeDays,
 *     sunPA,                       position angle of the Sun's direction, degrees
 *     northPoleTilt,               how far the north pole leans towards or away from us,
 *                                  degrees: equal to de, kept as its own name for the page
 *   }
 *
 * Depends on the globals NovaAstro (src/nova_astro.js) and NovaPlanets
 * (src/nova_planets.js), both loaded as plain scripts first.
 * No DOM, no network: the module runs unchanged in Node.
 */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.NovaMars = factory();
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const PI = Math.PI;
    const DEG = PI / 180, HOUR = PI / 12;
    const DAY_MS = 86400000;
    const LIGHT_DAYS_PER_AU = 0.0057755183;
    const EPS_J2000 = 23.4392911 * DEG;

    // TT - UTC. TT = TAI + 32.184 s and TAI - UTC = 37 s since 2017-01-01, so 69.184 s.
    // pck00011.tpc, "Time systems and reference frames": the rotation formulas take their
    // independent variable in TDB (TT is within 2 ms of it), epoch J2000. It belongs in W,
    // where it is 0.28 deg of Mars, and in the pole's T, where it is 2e-8 centuries.
    const TT_MINUS_UTC_MS = 69184;
    const TT_MINUS_UTC_DAYS = TT_MINUS_UTC_MS / DAY_MS;

    // IAU/WGCCRE Mars. SPICE pck00011.tpc BODY499_*.
    const POLE_RA0 = 317.68143, POLE_RA_RATE = -0.1061;       // degrees, per century
    const POLE_DEC0 = 52.88650, POLE_DEC_RATE = -0.0609;       // degrees, per century
    const W0 = 176.630, W_RATE = 350.89198226;                 // degrees, per day

    // Mars's nutation is not applied. The 2009 report the elements above come from gives
    // Mars no periodic terms, and pck00011.tpc's Mars entry carries none for those values
    // either: its nonzero BODY499_NUT_PREC_* terms belong to the 2015 pole, which is a
    // different pole (see the note above). This file had a three-term series with arguments
    // 352.9065 + 1.1733 T and so on; nothing I can point at sources it, and the whole
    // effect is under 0.001 deg, so it is gone rather than guessed at.

    const sin = Math.sin, cos = Math.cos;
    const mod = (a, n) => ((a % n) + n) % n;
    const clamp1 = x => (x > 1 ? 1 : x < -1 ? -1 : x);
    const centuries = utcMs => (utcMs / DAY_MS + 2440587.5 - 2451545.0) / 36525;

    function engine() {
        const P = typeof NovaPlanets !== 'undefined' ? NovaPlanets : null;
        if (!P) throw new Error('nova_mars: NovaPlanets is not loaded (src/nova_planets.js must come first)');
        const A_ = typeof NovaAstro !== 'undefined' ? NovaAstro : null;
        if (!A_) throw new Error('nova_mars: NovaAstro is not loaded (src/nova_astro.js must come first)');
        return { planets: P, astro: A_ };
    }

    const norm = v => Math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
    const unit = v => { const n = norm(v); return [v[0] / n, v[1] / n, v[2] / n]; };
    const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
    const apply = (M, v) => [
        M[0] * v[0] + M[1] * v[1] + M[2] * v[2],
        M[3] * v[0] + M[4] * v[1] + M[5] * v[2],
        M[6] * v[0] + M[7] * v[1] + M[8] * v[2],
    ];

    /** Unit vector Earth -> Mars, ICRF/J2000 astrometric, and the distances in AU. */
    function earthToMars(utcMs) {
        const p = engine().planets.position('mars', utcMs);
        const ra = p.raHours * HOUR, dec = p.decDeg * DEG, cd = cos(dec);
        return { u: [cd * cos(ra), cd * sin(ra), sin(dec)], dist: p.distAU, sunDist: p.sunDistAU };
    }

    /**
     * Unit vector Earth -> Sun, ICRF/J2000, and the distance in AU. Same reduction as
     * nova_jupiter.js: the Sun's geometric longitude on the mean equinox of date
     * (sunPosition().trueLon), less the precession in longitude, then the ecliptic of J2000
     * into the equator of J2000. The geometric place and not the apparent one, because the
     * apparent carries the annual aberration - an observing artefact, not a shift of the
     * Sun. Note trueLon is in RADIANS while the precession series is in degrees: prec is
     * the only term that carries DEG, and treating them all as degrees put the Sun 279 deg
     * out in longitude, which is what the Horizons SunSub-LAT check caught.
     */
    function earthToSun(utcMs) {
        const A_ = engine().astro;
        const s = A_.sunPosition(utcMs);
        const T = centuries(utcMs);
        const prec = (5029.0966 * T + 1.11113 * T * T - 0.000006 * T * T * T) / 3600;
        const lam = s.trueLon - prec * DEG;                // radians
        return { u: [cos(lam), sin(lam) * cos(EPS_J2000), sin(lam) * sin(EPS_J2000)], dist: s.dist };
    }

    /**
     * Mars's pole direction, ICRF/J2000, from the IAU/WGCCRE elements. Two linear terms
     * only: Mars is one of the bodies the 2009 report gives no periodic terms for, and the
     * Horizons check lands inside 0.007 deg in de without them.
     */
    function pole(utcMs) {
        const T = centuries(utcMs + TT_MINUS_UTC_MS);
        const ra = (POLE_RA0 + POLE_RA_RATE * T) * DEG;
        const dec = (POLE_DEC0 + POLE_DEC_RATE * T) * DEG;
        return [cos(dec) * cos(ra), cos(dec) * sin(ra), sin(dec)];
    }

    /** Position angle of Mars's north pole, pole and place both turned to the equator of
     * date: a position angle belongs to a frame, and the pole of the date frame is not the
     * pole of J2000. See nova_jupiter.js, where the same shortcut was 0.2 deg out. */
    function polePA(uM, utcMs) {
        return positionAngleOfDate(pole(utcMs), uM, utcMs);
    }

    /** Position angle of a J2000 direction v against Mars's place uM, both of date. */
    function positionAngleOfDate(v, uM, utcMs) {
        const M = engine().astro.frameFor(utcMs).M;
        return positionAngle(apply(M, v), apply(M, uM));
    }

    /** Position angle of a direction v against Mars (uM), north through east. */
    function positionAngle(v, uM) {
        const raJ = Math.atan2(uM[1], uM[0]), decJ = Math.asin(clamp1(uM[2]));
        const ra = Math.atan2(v[1], v[0]), dec = Math.asin(clamp1(v[2]));
        return mod(Math.atan2(cos(dec) * sin(ra - raJ),
            sin(dec) * cos(decJ) - cos(dec) * sin(decJ) * cos(ra - raJ)) / DEG, 360);
    }

    function geometry(utcMs) {
        const j = earthToMars(utcMs);
        const sun = earthToSun(utcMs);
        const p = pole(utcMs);

        const mars = [j.u[0] * j.dist, j.u[1] * j.dist, j.u[2] * j.dist];
        const sunV = [sun.u[0] * sun.dist, sun.u[1] * sun.dist, sun.u[2] * sun.dist];
        const mToSun = unit([sunV[0] - mars[0], sunV[1] - mars[1], sunV[2] - mars[2]]);
        const mToEarth = [-j.u[0], -j.u[1], -j.u[2]];

        // The body frame: z along the pole, x at the node of the J2000 equator on the
        // body equator, turned by W. Same construction as nova_jupiter's System III, TT
        // included: the WGCCRE model is defined with d in TT, not UT.
        const d = (utcMs / DAY_MS + 2440587.5 + TT_MINUS_UTC_DAYS - 2451545.0)
            - j.dist * LIGHT_DAYS_PER_AU;
        const W = mod(W0 + W_RATE * d, 360);
        // The node of the body equator on the J2000 equator, taken from the pole itself:
        // (-sin alpha, cos alpha, 0) for its own right ascension. Mars's pole has no
        // periodic terms, so this is the same as using POLE_RA0, but it is the definition.
        const node = unit(cross([0, 0, 1], p));
        const xb = unit([
            cos(W * DEG) * node[0] + sin(W * DEG) * cross(p, node)[0],
            cos(W * DEG) * node[1] + sin(W * DEG) * cross(p, node)[1],
            cos(W * DEG) * node[2] + sin(W * DEG) * cross(p, node)[2],
        ]);
        const yb = cross(p, xb);

        const de = Math.asin(clamp1(dot(p, mToEarth))) / DEG;
        const ds = Math.asin(clamp1(dot(p, mToSun))) / DEG;
        // atan2(yb, xb) is the sub-Earth point's *east* longitude in the body frame, so the
        // central meridian is its negative. This used to read W - that, which adds a whole
        // extra rotation (W runs, so the error is not even a constant): it put the CM 321
        // deg out at 2024.0 and was what the Horizons check caught. The result is the
        // planetographic (west-positive) longitude observers quote.
        const cm = mod(-Math.atan2(dot(mToEarth, yb), dot(mToEarth, xb)) / DEG, 360);

        return {
            cm, de, ds,
            polePA: polePA(j.u, utcMs),
            diameterArcsec: 9.36 / j.dist,                      // ephem's equatorial diameter at 1 AU
            distanceAU: j.dist,
            sunDistAU: j.sunDist,
            phaseAngleDeg: Math.acos(clamp1(dot(mToEarth, mToSun))) / DEG,
            lightTimeDays: j.dist * LIGHT_DAYS_PER_AU,
            sunPA: positionAngleOfDate(mToSun, j.u, utcMs),
            northPoleTilt: de,
        };
    }

    return { geometry, POLE_RA0, POLE_DEC0, W0, W_RATE };
});
