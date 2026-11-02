/*
 * nova_saturn.js — Saturn as a ringed globe and as a system of moons: B and B', the
 * planetocentric latitudes of the Earth and of the Sun above the ring plane, the position
 * angle of the ring's northern semiminor axis, the Saturnicentric longitude difference of
 * the Sun and the Earth in that plane, the axes of the apparent ellipse of the ring's
 * outer edge, and where the eight classical satellites stand on the sky.
 *
 * Maths only: which of it to show, and when, is the page's business.
 *
 * Accuracy is checked with tools/test_saturn.cjs (fixture tests/fixtures/saturn_reference.json,
 * built by tools/gen_saturn_fixture.py) against ephem's earth_tilt and sun_tilt and against
 * ephem's lambert-azimuthal satellite x/y/z, against Meeus's worked Examples 45.a and 46.a,
 * and against JPL Horizons' sub-Earth and sub-Sun latitudes and the ICRF pole. Any change to
 * this file must keep that check green.
 *
 * Pass marks (Rs = Saturn's equatorial radius), measured over the 1072 instants of
 * tests/fixtures/saturn_reference.json. Each is the worst case measured on that fixture,
 * with the mark set just above it:
 *   B             0.02 deg  (0.0192)   against ephem's earth_tilt
 *   B'            0.02 deg  (0.0176)   against ephem's sun_tilt
 *   delta U       0.02 deg  (0.0139)   against its own phase-angle identity
 *   a, b          a x Delta is 375.35 arcsec and b is a |sin B|, both exact; a against
 *                 375.35 / ephem's own Earth distance 0.13 arcsec (0.118), the 0.27 per
 *                 cent of it being nova_planets' Saturn distance and not this file's
 *   satellites    Rs, per moon. Mimas 0.06, Enceladus 0.035, Tethys 0.04, Dione 0.045,
 *                 Rhea 0.055, Titan 0.10, Hyperion 0.65, Iapetus 0.30
 *                 (0.0475 .. 0.0272 .. 0.0308 .. 0.0371 .. 0.0443 .. 0.0889 .. 0.5611 .. 0.2569)
 *   Example 45.a  B 0.03, B' 0.02, P 0.04, delta U 0.02 deg; a 0.15, b 0.06 arcsec
 *                 (0.0199, 0.0130, 0.0271, 0.0141, 0.1305, 0.0460)
 *   Example 46.a  worst 0.05 Rs over the eight (0.0261, on Iapetus)
 *   Horizons      sub-Earth latitude 0.02 deg (0.0195), sub-Sun 0.02 (0.0171), sub-solar
 *                 position angle 0.05 (0.0283) on the instants whose sub-solar offset is
 *                 clear of the disc centre, and the pole itself 0.001 deg (0.000001)
 *                 against N.Pole-RA / N.Pole-DC
 * (the worst-case table is also in tools/test_saturn.cjs's header, which is the authority.)
 *
 * Two of those marks need saying out loud, because they are wider than the rest.
 *   - The satellites. libastro's satmoon.c is a Dourneau / ch. 46 implementation as this
 *     file is, so ephem is a cross-check on the transcription and not an independent
 *     theory - but the two disagree, mostly on Hyperion and Iapetus. What settles which is
 *     right is Example 46.a: at Meeus's own instant for it, ephem stands 0.027, 0.035,
 *     0.039, 0.050, 0.032, 0.158, 0.223 and 0.477 Rs from the eight printed positions,
 *     while this file stands 0.0019, 0.0022, 0.0020, 0.0023, 0.0038, 0.0081, 0.0101 and
 *     0.0261 Rs. The transcription is good to the third decimal; the ephem mark is
 *     measuring libastro. The one thing that would move all eight at once is the unit, and
 *     that is corrected in the fixture - see tools/gen_saturn_fixture.py.
 *   - delta U. Neither ephem nor Horizons prints a ring-plane longitude, so there is no
 *     external value to check it against, and Example 45.a is the only printed one. Over
 *     the fixture it is held instead by the identity that ties it to the other three angles,
 *     cos(phase) = sin B sin B' + cos B cos B' cos(delta U), which is exact and is applied
 *     to this file's own B, B' and phase angle on every instant.
 *
 * Theory
 *   - Saturn's place and the frame: NovaPlanets.position('saturn') — astrometric J2000,
 *     light-time corrected — and NovaAstro.frameFor(), exactly as nova_jupiter.js does it.
 *   - The pole: the IAU/WGCCRE rotation elements
 *         alpha0 = 40.589 - 0.036 T,   delta0 = 83.537 - 0.004 T   (degrees, J2000)
 *     with T in Julian centuries from J2000. Source: Archinal et al., "Report of the IAU
 *     Working Group on Cartographic Coordinates and Rotational Elements: 2015", Celest.
 *     Mech. Dyn. Astr. 130:22 (2018), and the SPICE kernel pck00011.tpc, which carries
 *     them verbatim as
 *         BODY699_POLE_RA  = ( 40.589  -0.036  0. )
 *         BODY699_POLE_DEC = ( 83.537  -0.004  0. )
 *     pck00011.tpc carries no BODY699_NUT_PREC_RA / _DEC lines at all, so unlike Jupiter's
 *     Saturn's pole has no nutation terms in the kernel and none are applied. These are
 *     the same two numbers nova_planets.js uses for ringTiltDeg, so the card's B and the
 *     telescope view's ring tilt are one quantity by construction rather than two that
 *     have to be kept in step.
 *   - Ring geometry: Meeus, Astronomical Algorithms, 2nd ed., ch. 45. B and B' are the
 *     planetocentric latitudes of the Earth and of the Sun over the ring plane; ch. 45
 *     (45.1) writes each as an arcsine of the ecliptic elements i and Omega of that plane,
 *     but the angle is the same one and the pole form is what nova_planets.js already
 *     carries, so the pole form is used here. ch. 45's own i = 28.075216 - 0.012998 T and
 *     Omega = 169.508470 + 1.394681 T describe the same plane at J2000 to 0.023 deg in i
 *     and 0.019 deg in Omega and are not used; where ch. 45's series is needed instead of
 *     the pole — the ring plane's node on the ecliptic, for delta U — it is recovered from
 *     the pole rather than from those two.
 *     P is ch. 45 (45.5): the position angle of the northern semiminor axis of the apparent
 *     ellipse, which is also the position angle of the north pole of rotation.
 *     delta U is ch. 45 (45.3 and 45.4): the Saturnicentric longitudes of the Sun and of
 *     the Earth measured in the ring plane from its ascending node, taken as their
 *     difference, 0..180 deg.
 *     a and b are ch. 45's axes of the outer edge of the outer ring: a = 375.35 / Delta
 *     arcseconds for the major axis — a *diameter*, not a radius, which is what makes
 *     b = a |sin B| come out as ch. 45 prints it — and b the minor axis. Delta is the
 *     Earth-Saturn distance in AU. Meeus's 375.35 implies an outer edge radius of
 *     136,115 km against the modern 136,775 km, 0.48 per cent, and the value is kept as
 *     printed so that Example 45.a reproduces exactly. The inner edges of the rings come
 *     from ch. 45's own factors of a, printed as a note to ring_parameters: 0.8801 for the
 *     inner edge of the outer ring, 0.8599 for the outer edge of the inner ring, 0.6650
 *     for its inner edge, 0.5486 for the inner edge of the dusky ring. The first two are
 *     the Cassini division, and they are the whole of what the page needs to draw the
 *     division as a gap rather than a line.
 *   - The satellites: Meeus, Astronomical Algorithms, 2nd ed., ch. 46 — Dourneau's theory
 *     as Meeus tabulates it, which is where every constant in satelliteElements() and in
 *     the per-moon functions comes from. The coefficients were transcribed against the MIT
 *     transcription in astronomia (src/saturnmoons.js, © 2013 Sonia Keys) and cross-checked
 *     against pymeeus 0.5.12, which implements ch. 45 but not ch. 46. The theory is written
 *     on the B1950 ecliptic, so Saturn's geocentric longitude and latitude are precessed
 *     back from J2000 by eclipticPrecess() before use.
 *   - Ecliptic precession: Meeus ch. 21, formulas 21.5 and 21.7, the same series
 *     astronomia's EclipticPrecessor carries. Only the one pair of epochs this file needs
 *     is evaluated — J2000 out, B1950 = JD 2433282.4235 in (astronomia base.js, "B1900,
 *     B1950 from p. 133", i.e. Meeus p. 133).
 *
 * Two notes on the references, both of which the parity check depends on:
 *   - ephem's Saturn.earth_tilt and .sun_tilt are libastro's own ring tilt. libastro's
 *     BDL Saturn data is good from 1999 to 2039 and produces garbage during 2040, which is
 *     why the fixture stops at 2039-12-31; nothing here is validated beyond that, and there
 *     is nothing in ch. 45 or ch. 46 that would break there — the limit is the reference's,
 *     not the theory's.
 *   - ephem's satellite x/y/z come from libastro's satmoon.c, which is also a
 *     Dourneau/Meeus ch. 46 implementation. It is therefore a cross-check on the
 *     transcription rather than an independent theory, and the harness says so. The
 *     independent checks on ch. 46 here are Meeus's Example 46.a, and the k table below,
 *     whose eight entries each reproduce their satellite's mean orbital speed 2 pi a / P
 *     to 0.3 per cent when read as 2475 / (0.0057755 k) Saturn radii a day, against the
 *     modern semi-major axes and periods — Hyperion alone is 1.6, its orbit being
 *     eccentric and perturbed by Titan.
 *
 * Conventions
 *   - Degrees everywhere. B is positive when the Earth is north of the ring plane, that
 *     is when the northern face of the rings is the one turned towards us; B' the same for
 *     the Sun, and so for the face the rings are lit on. Where the two disagree the rings
 *     are lit on one face and seen from the other, which is what delta U measures.
 *   - polePA is Meeus's P, north through east, and is the position angle of the north pole
 *     of rotation and of the northern semiminor axis of the ring ellipse alike.
 *   - moons(): x east on the sky, y south, z towards the Earth, in Saturn equatorial radii.
 *     This is ephem's own convention, so the parity check is a direct comparison.
 *   - Times are UTC. Positions use UTC as TT, as in nova_astro.js and nova_jupiter.js.
 *
 * geometry(utcMs) -> {
 *     B, Bprime,                   planetocentric latitude of the Earth, of the Sun, deg
 *     polePA,                      position angle of the north pole / northern semiminor
 *     deltaU,                      ring-plane longitude difference, Sun - Earth, 0..180
 *     aArcsec, bArcsec,            major and minor axes of the ring's outer edge
 *     distanceAU, sunDistAU,       from the Earth, from the Sun
 *     phaseAngleDeg,               Sun-Saturn-Earth
 *     lightTimeDays,
 *     sunPA,                       position angle of the Sun's direction, degrees
 *   }
 * moons(utcMs) -> [{ name, x, y, z, r, west }] in the order MOONS; r is the distance from
 *     Saturn's centre in the sky plane and west is true west of Saturn's centre on the sky
 * elongations(startUtcMs, endUtcMs) -> [{ utcMs, moon, side, distanceRs }], the greatest
 *     western elongations of Iapetus, which is when its bright trailing face is towards us
 *
 * Depends on the globals NovaAstro (src/nova_astro.js) and NovaPlanets (src/nova_planets.js),
 * both loaded as plain scripts first. No DOM, no network: the module runs unchanged in Node.
 */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.NovaSaturn = factory();
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const PI = Math.PI, TAU = 2 * PI;
    const DEG = PI / 180, ARCSEC = DEG / 3600;
    const DAY_MS = 86400000;
    const LIGHT_DAYS_PER_AU = 0.0057755183;       // Meeus ch. 45 and 46, as in nova_jupiter.js
    const EPS_J2000 = 23.4392911 * DEG;
    const sin = Math.sin, cos = Math.cos;

    function engine() {
        const P = typeof NovaPlanets !== 'undefined' ? NovaPlanets : null;
        if (!P) throw new Error('nova_saturn: NovaPlanets is not loaded (src/nova_planets.js must come first)');
        const A_ = typeof NovaAstro !== 'undefined' ? NovaAstro : null;
        if (!A_) throw new Error('nova_saturn: NovaAstro is not loaded (src/nova_astro.js must come first)');
        return { planets: P, astro: A_ };
    }

    const norm = v => Math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
    const unit = v => { const n = norm(v); return [v[0] / n, v[1] / n, v[2] / n]; };
    const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
    const clamp1 = x => (x > 1 ? 1 : x < -1 ? -1 : x);
    const mod = (a, n) => ((a % n) + n) % n;
    const apply = (M, v) => [
        M[0] * v[0] + M[1] * v[1] + M[2] * v[2],
        M[3] * v[0] + M[4] * v[1] + M[5] * v[2],
        M[6] * v[0] + M[7] * v[1] + M[8] * v[2],
    ];

    const jdOf = utcMs => utcMs / DAY_MS + 2440587.5;
    const centuries = utcMs => (jdOf(utcMs) - 2451545.0) / 36525;

    // ------------------------------------------------------------------ precession

    /**
     * Ecliptic precession between two Julian epochs, Meeus ch. 21 formulas 21.5 and 21.7.
     * Radians in, radians out. The coefficients are Meeus's, in arcseconds per century of
     * the interval; astronomia's EclipticPrecessor (MIT) carries the same ones. Only the
     * epochFrom = 2000, epochTo = B1950 pair below is used, and it is evaluated once at
     * load: eta = -0.0065306 deg, pi = 175.00424 deg, p = -0.698496 deg.
     */
    function eclipticPrecess(lon, lat, epochFrom, epochTo) {
        const T = (epochFrom - 2000) * 0.01;
        // (21.5), p. 136. The T-dependent forms are only needed away from J2000.
        let etaC = [47.0029 * ARCSEC, -0.03302 * ARCSEC, 0.000060 * ARCSEC];
        let piC = [174.876384 * DEG, -869.8089 * ARCSEC, 0.03536 * ARCSEC];
        let pC = [5029.0966 * ARCSEC, 1.11113 * ARCSEC, -0.000006 * ARCSEC];
        if (T !== 0) {
            const horner = (x, c) => c[0] + x * (c[1] + x * c[2]);
            etaC = [horner(T, [47.0029 * ARCSEC, -0.06603 * ARCSEC, 0.000598 * ARCSEC]),
                -0.03302 * ARCSEC + 0.000598 * ARCSEC * T, 0.000060 * ARCSEC];
            piC = [horner(T, [174.876384 * DEG, 3289.4789 * ARCSEC, 0.60622 * ARCSEC]),
                -869.8089 * ARCSEC - 0.50491 * ARCSEC * T, 0.03536 * ARCSEC];
            pC = [horner(T, [5029.0966 * ARCSEC, 2.22226 * ARCSEC, -0.000042 * ARCSEC]),
                1.11113 * ARCSEC - 0.000042 * ARCSEC * T, -0.000006 * ARCSEC];
        }
        const t = (epochTo - epochFrom) * 0.01;
        const eta = (etaC[0] + t * (etaC[1] + t * etaC[2])) * t;
        const pi0 = piC[0] + t * (piC[1] + t * piC[2]);
        const p = (pC[0] + t * (pC[1] + t * pC[2])) * t;
        const se = sin(eta), ce = cos(eta);
        const sb = sin(lat), cb = cos(lat);
        const sd = sin(pi0 - lon), cd = cos(pi0 - lon);
        const A_ = ce * cb * sd - se * sb;
        const B_ = cb * cd;
        const C_ = ce * sb + se * cb * sd;
        return { lon: p + pi0 - Math.atan2(A_, B_), lat: Math.asin(clamp1(C_)) };
    }

    // ch. 21's epochs are Julian years, not Julian days. B1950.0 is JD 2433282.4235
    // (astronomia base.js, "B1900, B1950 from p. 133"), which is Julian year 1949.999791.
    const J2000_JD = 2451545.0;
    const B1950_JD = 2433282.4235;
    const julianYear = jd => 2000.0 + (jd - J2000_JD) / 365.25;

    /** J2000 out, B1950 in, the one pair ch. 46 needs. */
    const b1950Of = (lonRad, latRad) =>
        eclipticPrecess(lonRad, latRad, 2000, julianYear(B1950_JD));

    // ------------------------------------------------------------------ directions

    /** Unit vector Earth -> Saturn, ICRF/J2000 astrometric, and the distances in AU. */
    function earthToSaturn(utcMs) {
        const p = engine().planets.position('saturn', utcMs);
        const ra = p.raHours * PI / 12, dec = p.decDeg * DEG, cd = cos(dec);
        return { u: [cd * cos(ra), cd * sin(ra), sin(dec)], dist: p.distAU, sunDist: p.sunDistAU };
    }

    /**
     * Unit vector Earth -> Sun, ICRF/J2000, and the distance in AU. Geometric, not
     * apparent: the 20.5" of annual aberration is an observing artefact, not a shift of the
     * Sun. Identical to nova_jupiter.js's earthToSun(), down to the radians/degrees trap in
     * the precession term, which is why it is written the same way.
     */
    function earthToSun(utcMs) {
        const s = engine().astro.sunPosition(utcMs);
        const T = centuries(utcMs);
        const prec = (5029.0966 * T + 1.11113 * T * T - 0.000006 * T * T * T) / 3600;
        const lam = s.trueLon - prec * DEG;
        return { u: [cos(lam), sin(lam) * cos(EPS_J2000), sin(lam) * sin(EPS_J2000)], dist: s.dist };
    }

    /**
     * Saturn's pole, ICRF/J2000. IAU/WGCCRE, SPICE pck00011.tpc BODY699_POLE_RA /
     * BODY699_POLE_DEC; see the header. No nutation terms: the kernel carries none for
     * Saturn, and no precession of the pole beyond the two linear rates is applied.
     */
    function pole(utcMs) {
        const T = centuries(utcMs);
        const ra = (40.589 - 0.036 * T) * DEG, dec = (83.537 - 0.004 * T) * DEG;
        return [cos(dec) * cos(ra), cos(dec) * sin(ra), sin(dec)];
    }

    /** Position angle of a J2000 direction v against Saturn's place uS, north through east. */
    function positionAngle(v, uS) {
        const raS = Math.atan2(uS[1], uS[0]), decS = Math.asin(clamp1(uS[2]));
        const ra = Math.atan2(v[1], v[0]), dec = Math.asin(clamp1(v[2]));
        return mod(Math.atan2(cos(dec) * sin(ra - raS),
            sin(dec) * cos(decS) - cos(dec) * sin(decS) * cos(ra - raS)) / DEG, 360);
    }

    /**
     * Both directions turned to the equator of date before the angle is taken. A position
     * angle is measured from the pole of the frame it is written in, so mixing frames is up
     * to 0.2 deg out, which is the trap nova_jupiter.js records on the same function.
     */
    function positionAngleOfDate(v, uS, utcMs) {
        const M = engine().astro.frameFor(utcMs).M;
        return positionAngle(apply(M, v), apply(M, uS));
    }

    /** Saturn's geocentric ecliptic longitude and latitude in J2000, radians. */
    function saturnEclipticJ2000(uS) {
        const ce = cos(EPS_J2000), se = sin(EPS_J2000);
        const y = uS[1] * ce + uS[2] * se, z = -uS[1] * se + uS[2] * ce;
        return { lon: Math.atan2(y, uS[0]), lat: Math.asin(clamp1(z)) };
    }

    /** A J2000 equatorial unit vector in the ecliptic frame of J2000. */
    function toEclipticJ2000(v) {
        const ce = cos(EPS_J2000), se = sin(EPS_J2000);
        return [v[0], v[1] * ce + v[2] * se, -v[1] * se + v[2] * ce];
    }


    // ------------------------------------------------------------------ ring geometry

    // Meeus ch. 45's factors of a, the major axis of the outer edge of the outer ring, for
    // the edges that make up the ring system as it is drawn. Source: the note printed under
    // ring_parameters in ch. 45. The first two are the Cassini division.
    const RING_EDGES = {
        outerOuter: 1.0,        // outer edge of the A ring
        outerInner: 0.8801,     // inner edge of the A ring / outer edge of the Cassini division
        innerOuter: 0.8599,     // outer edge of the B ring / inner edge of the Cassini division
        innerInner: 0.6650,     // inner edge of the B ring
        duskyInner: 0.5486,     // inner edge of the C ring
    };

    // ch. 45's own elements of the ring plane, referred to the mean ecliptic of date. Not
    // used for B, B' or P (the pole carries those); they are the published check on the
    // plane those quantities are taken in, and tools/test_saturn.cjs compares them.
    const RING_I0 = 28.075216, RING_I1 = -0.012998, RING_I2 = 0.000004;
    const RING_N0 = 169.508470, RING_N1 = 1.394681, RING_N2 = 0.000412;

    function ringPlaneElements(utcMs) {
        const T = centuries(utcMs);
        return {
            i: RING_I0 + RING_I1 * T + RING_I2 * T * T,
            node: RING_N0 + RING_N1 * T + RING_N2 * T * T,
        };
    }

    /**
     * The ring plane's ascending node on the mean ecliptic of date, as a unit vector in the
     * ecliptic frame of date, and the plane's normal, the same. Recovered from the pole
     * rather than from ch. 45's i and Omega so that delta U is measured in exactly the plane
     * B and B' are taken against: the pole is at ecliptic latitude 90 - i and longitude
     * Omega - 90, so Omega = lambda_pole + 90.
     */
    function ringNode(poleEclOfDate) {
        const lam = Math.atan2(poleEclOfDate[1], poleEclOfDate[0]);
        const beta = Math.asin(clamp1(poleEclOfDate[2]));
        const node = lam + PI / 2;
        const n = [cos(node), sin(node), 0];
        return {
            node: mod(node, TAU), i: PI / 2 - beta,
            N: n, m: cross(poleEclOfDate, n),
        };
    }

    /**
     * The Saturnicentric longitude of a direction in the ring plane, measured from the
     * plane's ascending node on the ecliptic. This is ch. 45's U1 and U2: with N the node
     * direction and m = n_ring x N, U = atan2(v . m, v . N), which expands to ch. 45's
     *   atan2(sin i sin b + cos i cos b sin(l - Omega), cos b cos(l - Omega)).
     */
    function ringLongitude(vEclOfDate, ring) {
        return mod(Math.atan2(dot(vEclOfDate, ring.m), dot(vEclOfDate, ring.N)), TAU);
    }

    function geometry(utcMs) {
        const j = earthToSaturn(utcMs);
        const sun = earthToSun(utcMs);
        const p = pole(utcMs);

        // Sun - Saturn from the two geocentric vectors, all in J2000
        const sat = [j.u[0] * j.dist, j.u[1] * j.dist, j.u[2] * j.dist];
        const sunV = [sun.u[0] * sun.dist, sun.u[1] * sun.dist, sun.u[2] * sun.dist];
        const sToSun = unit([sunV[0] - sat[0], sunV[1] - sat[1], sunV[2] - sat[2]]);
        const sToEarth = [-j.u[0], -j.u[1], -j.u[2]];

        const B = Math.asin(clamp1(dot(p, sToEarth))) / DEG;
        const Bprime = Math.asin(clamp1(dot(p, sToSun))) / DEG;

        // The ring plane's node, and with it delta U, are written on the mean ecliptic of
        // date. The pole reaches it through J2000: it is already J2000 equatorial, so the
        // fixed obliquity takes it to the J2000 ecliptic and ch. 21's precession from there.
        const pEcl = toEclipticJ2000(p);
        const pEclDate = { lon: 0, lat: 0 };
        {
            const pl = Math.atan2(pEcl[1], pEcl[0]), pb = Math.asin(clamp1(pEcl[2]));
            const q = eclipticPrecess(pl, pb, 2000, 2000 + centuries(utcMs) * 100);
            pEclDate.lon = q.lon; pEclDate.lat = q.lat;
        }
        const poleDate = [cos(pEclDate.lat) * cos(pEclDate.lon),
            cos(pEclDate.lat) * sin(pEclDate.lon), sin(pEclDate.lat)];
        const ring = ringNode(poleDate);
        const ecl = v => toEclipticJ2000(unit(v));
        const uEarth = ringLongitude(ecl(sToEarth), ring);
        const uSun = ringLongitude(ecl(sToSun), ring);
        let deltaU = Math.abs(uSun - uEarth) / DEG;
        if (deltaU > 180) deltaU = 180 - (deltaU - 180);

        // ch. 45's axes of the outer edge of the outer ring, in arcseconds
        const aArcsec = 375.35 / j.dist;
        const bArcsec = aArcsec * Math.abs(sin(B * DEG));

        return {
            B, Bprime,
            polePA: positionAngleOfDate(p, j.u, utcMs),
            deltaU,
            aArcsec, bArcsec,
            // ch. 45's two element sets for the one ring plane: its printed series, and the
            // same recovered from the IAU pole. tools/test_saturn.cjs compares them.
            ringPlane: ringPlaneElements(utcMs),
            ringFromPole: { i: ring.i / DEG, node: ring.node / DEG },
            distanceAU: j.dist,
            sunDistAU: j.sunDist,
            phaseAngleDeg: Math.acos(clamp1(dot(sToEarth, sToSun))) / DEG,
            lightTimeDays: j.dist * LIGHT_DAYS_PER_AU,
            sunPA: positionAngleOfDate(sToSun, j.u, utcMs),
        };
    }

    // ------------------------------------------------------------------ ch. 46: satellites

    const MOONS = ['Mimas', 'Enceladus', 'Tethys', 'Dione', 'Rhea', 'Titan', 'Hyperion', 'Iapetus'];

    // Meeus ch. 46's light-time constants, one per satellite. Each is the count that turns
    // the satellite's own sky-plane speed into the differential light-time correction to X;
    // read as 2475 / (0.0057755 k) Saturn radii a day they reproduce 2 pi a / P for all
    // eight to 0.3 per cent against the modern elements (Hyperion 1.6), which is the check
    // that the table and the semi-major axes below belong to the same satellites. Index 0
    // is unused: ch. 46's arrays are 1-based.
    const K_LIGHT = [0, 20947, 23715, 26382, 29876, 35313, 53800, 59222, 91820];

    // ch. 46's own Saturn radii per astronomical unit and light time. Meeus's 2475 implies
    // 60,443 km, against the 60,268 km in use elsewhere; the quantity it enters is a
    // correction of order Z/Delta, so the 0.3 per cent is immaterial and the printed value
    // is kept.
    const RS_PER_AU_CH46 = 2475;

    /**
     * Meeus ch. 46's shared angles, in a single object, exactly as the chapter numbers them.
     * Every constant is as printed there.
     */
    function sharedAngles(jde) {
        const t1 = jde - 2411093.0;
        const t2 = t1 / 365.25;
        const t3 = (jde - 2433282.423) / 365.25 + 1950.0;
        const t4 = jde - 2411368.0;
        const t5 = t4 / 365.25;
        const t6 = jde - 2415020.0;
        const t7 = t6 / 36525.0;
        const t8 = t6 / 365.25;
        const t9 = (jde - 2442000.5) / 365.25;
        const t10 = jde - 2409786.0;
        const t11 = t10 / 36525.0;

        const W0 = (5.095 * (t3 - 1866.39)) * DEG;
        const W1 = (74.4 + 32.39 * t2) * DEG;
        const W2 = (134.3 + 92.62 * t2) * DEG;
        const W3 = (42.0 - 0.5118 * t5) * DEG;
        const W4 = (276.59 + 0.5118 * t5) * DEG;
        const W5 = (267.2635 + 1222.1136 * t7) * DEG;
        const W6 = (175.4762 + 1221.5515 * t7) * DEG;
        const W7 = (2.4891 + 0.002435 * t7) * DEG;
        const W8 = (113.35 - 0.2597 * t7) * DEG;

        return {
            t1, t2, t3, t4, t5, t6, t7, t8, t9, t10, t11,
            W0, W1, W2, W3, W4, W5, W6, W7, W8,
            s1: sin(28.0817 * DEG), c1: cos(28.0817 * DEG),
            s2: sin(168.8112 * DEG), c2: cos(168.8112 * DEG),
            e1: 0.05589 - 0.000346 * t7,
            sW0: sin(W0), s3W0: sin(3 * W0), s5W0: sin(5 * W0),
            sW1: sin(W1), sW2: sin(W2), sW3: sin(W3), cW3: cos(W3),
            sW4: sin(W4), cW4: cos(W4), sW7: sin(W7), cW7: cos(W7),
        };
    }

    /**
     * ch. 46's subr: an elliptical orbit's true longitude, radius, and the node and
     * inclination that put it in the ring plane. lambda' is the mean longitude, p the
     * longitude of pericentre, e the eccentricity, a the semi-major axis, Omega and i the
     * node and inclination.
     */
    function subr(q, lamPrime, p, e, a, node, inc) {
        const M = lamPrime - p;
        const e2 = e * e, e3 = e2 * e, e4 = e2 * e2, e5 = e3 * e2;
        const C = (2 * e - 0.25 * e3 + 0.0520833333 * e5) * sin(M)
            + (1.25 * e2 - 0.458333333 * e4) * sin(2 * M)
            + (1.083333333 * e3 - 0.671875 * e5) * sin(3 * M)
            + 1.072917 * e4 * sin(4 * M) + 1.142708 * e5 * sin(5 * M);
        const r = a * (1 - e2) / (1 + e * cos(M + C));
        const g = node - 168.8112 * DEG;
        const si = sin(inc), ci = cos(inc), sg = sin(g), cg = cos(g);
        const a1 = si * sg;
        const a2 = q.c1 * si * cg - q.s1 * ci;
        const gam = Math.asin(clamp1(Math.hypot(a1, a2)));
        const u = Math.atan2(a1, a2);
        const h = q.c1 * si - q.s1 * ci * cg;
        const psi = Math.atan2(q.s1 * sg, h);
        return { lam: lamPrime + C + u - g - psi, r, gam, node: 168.8112 * DEG + u };
    }

    /** Mimas. */
    function mimas(q) {
        const L = (127.64 + 381.994497 * q.t1) * DEG
            - 43.57 * DEG * q.sW0 - 0.72 * DEG * q.s3W0 - 0.02144 * DEG * q.s5W0;
        const p = (106.1 + 365.549 * q.t2) * DEG;
        const C = 2.18287 * DEG * sin(L - p) + 0.025988 * DEG * sin(2 * (L - p))
            + 0.00043 * DEG * sin(3 * (L - p));
        return {
            lam: L + C,
            r: 3.06879 / (1 + 0.01905 * cos(L - p + C)),
            gam: 1.563 * DEG,
            node: (54.5 - 365.072 * q.t2) * DEG,
        };
    }

    /** Enceladus. */
    function enceladus(q) {
        const L = (200.317 + 262.7319002 * q.t1) * DEG + 0.25667 * DEG * q.sW1 + 0.20883 * DEG * q.sW2;
        const p = (309.107 + 123.44121 * q.t2) * DEG;
        const C = 0.55577 * DEG * sin(L - p) + 0.00168 * DEG * sin(2 * (L - p));
        return {
            lam: L + C,
            r: 3.94118 / (1 + 0.00485 * cos(L - p + C)),
            gam: 0.0262 * DEG,
            node: (348.0 - 151.95 * q.t2) * DEG,
        };
    }

    /** Tethys. */
    function tethys(q) {
        return {
            lam: (285.306 + 190.69791226 * q.t1) * DEG + 2.063 * DEG * q.sW0
                + 0.03409 * DEG * q.s3W0 + 0.001015 * DEG * q.s5W0,
            r: 4.880998,
            gam: 1.0976 * DEG,
            node: (111.33 - 72.2441 * q.t2) * DEG,
        };
    }

    /** Dione. */
    function dione(q) {
        const L = (254.712 + 131.53493193 * q.t1) * DEG - 0.0215 * DEG * q.sW1 - 0.01733 * DEG * q.sW2;
        const p = (174.8 + 30.82 * q.t2) * DEG;
        const C = 0.24717 * DEG * sin(L - p) + 0.00033 * DEG * sin(2 * (L - p));
        return {
            lam: L + C,
            r: 6.24871 / (1 + 0.002157 * cos(L - p + C)),
            gam: 0.0139 * DEG,
            node: (232.0 - 30.27 * q.t2) * DEG,
        };
    }

    /** Rhea. Its orbit is the first of the four ch. 46 gives through subr(). */
    function rhea(q) {
        const pPrime = (342.7 + 10.057 * q.t2) * DEG;
        const a1 = 0.000265 * sin(pPrime) + 0.001 * q.sW4;
        const a2 = 0.000265 * cos(pPrime) + 0.001 * q.cW4;
        const e = Math.hypot(a1, a2);
        const p = Math.atan2(a1, a2);
        const N = (345.0 - 10.057 * q.t2) * DEG;
        const lamPrime = (359.244 + 79.6900472 * q.t1) * DEG + 0.086754 * DEG * sin(N);
        const inc = (28.0362 + 0.346898 * cos(N)) * DEG + 0.0193 * DEG * q.cW3;
        const node = (168.8034 + 0.736936 * sin(N)) * DEG + 0.041 * DEG * q.sW3;
        return subr(q, lamPrime, p, e, 8.725924, node, inc);
    }

    /** Titan. */
    function titan(q) {
        const L = (261.1582 + 22.57697855 * q.t4) * DEG + 0.074025 * DEG * q.sW3;
        const iPrime = (27.45141 + 0.295999 * q.cW3) * DEG;
        const nodePrime = (168.66925 + 0.628808 * q.sW3) * DEG;
        const si = sin(iPrime), ci = cos(iPrime);
        const a1 = q.sW7 * sin(nodePrime - q.W8);
        const a2 = q.cW7 * si - q.sW7 * ci * cos(nodePrime - q.W8);
        const psi = Math.atan2(a1, a2);
        const s = Math.hypot(a1, a2);
        const g0 = 102.8623 * DEG;
        let g = q.W4 - nodePrime - psi;
        let pi0 = 0;
        for (let i = 0; i < 3; i++) {
            pi0 = q.W4 + 0.37515 * DEG * (sin(2 * g) - sin(2 * g0));
            g = pi0 - nodePrime - psi;
        }
        const ePrime = 0.029092 + 0.00019048 * (cos(2 * g) - cos(2 * g0));
        const qq = 2 * (q.W5 - pi0);
        const b1 = si * sin(nodePrime - q.W8);
        const b2 = q.cW7 * si * cos(nodePrime - q.W8) - q.sW7 * ci;
        const theta = Math.atan2(b1, b2) + q.W8;
        const e = ePrime + 0.002778797 * ePrime * cos(qq);
        const p = pi0 + 0.159215 * DEG * sin(qq);
        const u = 2 * q.W5 - 2 * theta + psi;
        const h = 0.9375 * ePrime * ePrime * sin(qq) + 0.1875 * s * s * sin(2 * (q.W5 - theta));
        const lamPrime = L - 0.254744 * DEG
            * (q.e1 * sin(q.W6) + 0.75 * q.e1 * q.e1 * sin(2 * q.W6) + h);
        const inc = iPrime + 0.031843 * DEG * s * cos(u);
        const node = nodePrime + 0.031843 * DEG * s * sin(u) / si;
        return subr(q, lamPrime, p, e, 20.216193, node, inc);
    }

    /** Hyperion. */
    function hyperion(q) {
        const eta = (92.39 + 0.5621071 * q.t6) * DEG;
        const zeta = (148.19 - 19.18 * q.t8) * DEG;
        const theta = (184.8 - 35.41 * q.t9) * DEG;
        const thetaPrime = theta - 7.5 * DEG;
        const as = (176.0 + 12.22 * q.t8) * DEG;
        const bs = (8.0 + 24.44 * q.t8) * DEG;
        const cs = bs + 5 * DEG;
        const pi0 = (69.898 - 18.67088 * q.t8) * DEG;
        const phi = 2 * (pi0 - q.W5);
        const chi = (94.9 - 2.292 * q.t8) * DEG;
        const a = 24.50601 - 0.08686 * cos(eta) - 0.00166 * cos(zeta + eta) + 0.00175 * cos(zeta - eta);
        const e = 0.103458 - 0.004099 * cos(eta) - 0.000167 * cos(zeta + eta)
            + 0.000235 * cos(zeta - eta) + 0.02303 * cos(zeta) - 0.00212 * cos(2 * zeta)
            + 0.000151 * cos(3 * zeta) + 0.00013 * cos(phi);
        const p = pi0 + 0.15648 * DEG * sin(chi) - 0.4457 * DEG * sin(eta)
            - 0.2657 * DEG * sin(zeta + eta) - 0.3573 * DEG * sin(zeta - eta)
            - 12.872 * DEG * sin(zeta) + 1.668 * DEG * sin(2 * zeta)
            - 0.2419 * DEG * sin(3 * zeta) - 0.07 * DEG * sin(phi);
        const lamPrime = (177.047 + 16.91993829 * q.t6) * DEG + 0.15648 * DEG * sin(chi)
            + 9.142 * DEG * sin(eta) + 0.007 * DEG * sin(2 * eta) - 0.014 * DEG * sin(3 * eta)
            + 0.2275 * DEG * sin(zeta + eta) + 0.2112 * DEG * sin(zeta - eta)
            - 0.26 * DEG * sin(zeta) - 0.0098 * DEG * sin(2 * zeta)
            - 0.013 * DEG * sin(as) + 0.017 * DEG * sin(bs) - 0.0303 * DEG * sin(phi);
        const inc = (27.3347 + 0.6434886 * cos(chi)) * DEG + 0.315 * DEG * q.cW3
            + 0.018 * DEG * cos(theta) - 0.018 * DEG * cos(cs);
        const node = (168.6812 + 1.40136 * cos(chi)) * DEG + 0.68599 * DEG * q.sW3
            - 0.0392 * DEG * sin(cs) + 0.0366 * DEG * sin(thetaPrime);
        return subr(q, lamPrime, p, e, a, node, inc);
    }

    /** Iapetus. The one satellite ch. 46 gives an opposition-surge term for. */
    function iapetus(q) {
        const L = (261.1582 + 22.57697855 * q.t4) * DEG;
        const piPrime = (91.796 + 0.562 * q.t7) * DEG;
        const psi = (4.367 - 0.195 * q.t7) * DEG;
        const theta = (146.819 - 3.198 * q.t7) * DEG;
        const phi = (60.47 + 1.521 * q.t7) * DEG;
        const Phi = (205.055 - 2.091 * q.t7) * DEG;
        const ePrime = 0.028298 + 0.001156 * q.t11;
        const pi0 = (352.91 + 11.71 * q.t11) * DEG;
        const mu = (76.3852 + 4.53795125 * q.t10) * DEG;
        const iPrime = (18.4602 - 0.9518 * q.t11 - 0.072 * q.t11 * q.t11
            + 0.0054 * q.t11 * q.t11 * q.t11) * DEG;
        const nodePrime = (143.198 - 3.919 * q.t11 + 0.116 * q.t11 * q.t11
            + 0.008 * q.t11 * q.t11 * q.t11) * DEG;
        const l = mu - pi0;
        const g = pi0 - nodePrime - psi;
        const g1 = pi0 - nodePrime - phi;
        const ls = q.W5 - piPrime;
        const gs = piPrime - theta;
        const lT = L - q.W4;
        const gT = q.W4 - Phi;
        const u1 = 2 * (l + g - ls - gs);
        const u2 = l + g1 - lT - gT;
        const u3 = l + 2 * (g - ls - gs);
        const u4 = lT + gT - g1;
        const u5 = 2 * (ls + gs);
        const a = 58.935028 + 0.004638 * cos(u1) + 0.058222 * cos(u2);
        const e = ePrime - 0.0014097 * cos(g1 - gT) + 0.0003733 * cos(u5 - 2 * g)
            + 0.000118 * cos(u3) + 0.0002408 * cos(l) + 0.0002849 * cos(l + u2)
            + 0.000619 * cos(u4);
        const w = 0.08077 * DEG * sin(g1 - gT) + 0.02139 * DEG * sin(u5 - 2 * g)
            - 0.00676 * DEG * sin(u3) + 0.0138 * DEG * sin(l)
            + 0.01632 * DEG * sin(l + u2) + 0.03547 * DEG * sin(u4);
        const p = pi0 + w / ePrime;
        const lamPrime = mu - 0.04299 * DEG * sin(u2) - 0.00789 * DEG * sin(u1)
            - 0.06312 * DEG * sin(ls) - 0.00295 * DEG * sin(2 * ls)
            - 0.02231 * DEG * sin(u5) + 0.0065 * DEG * sin(u5 + psi);
        const inc = iPrime + 0.04204 * DEG * cos(u5 + psi) + 0.00235 * DEG * cos(l + g1 + lT + gT + phi)
            + 0.0036 * DEG * cos(u2 + phi);
        const wPrime = 0.04204 * DEG * sin(u5 + psi) + 0.00235 * DEG * sin(l + g1 + lT + gT + phi)
            + 0.00358 * DEG * sin(u2 + phi);
        const node = nodePrime + wPrime / sin(iPrime);
        return subr(q, lamPrime, p, e, a, node, inc);
    }

    const MOON_FUNCS = [mimas, enceladus, tethys, dione, rhea, titan, hyperion, iapetus];

    /**
     * ch. 46's positions(), the whole chapter at once. Returns Meeus's own (X, Y, Z): X and
     * Y in the plane of the sky, Z along the Earth-Saturn line with Z positive away from the
     * Earth, all in Saturn equatorial radii. The caller turns that into whatever frame it
     * wants — moons() below turns it into ephem's.
     *
     * The theory is written on the B1950 ecliptic, so Saturn's geocentric longitude and
     * latitude are precessed back from J2000 to reach it; ch. 46 does the same from the
     * equinox of date. phi and beta there are what the light-time-corrected heliocentric
     * place reduces to geocentrically, which is the astrometric J2000 place NovaPlanets
     * already returns.
     */
    function meeusPositions(jde, distAU, uSaturn) {
        const e = saturnEclipticJ2000(uSaturn);
        const b = b1950Of(e.lon, e.lat);
        const lam0 = b.lon, beta0 = b.lat;

        const q = sharedAngles(jde);
        const s4 = [null];
        for (let i = 0; i < 8; i++) s4.push(MOON_FUNCS[i](q));

        const X = new Array(9).fill(0), Y = new Array(9).fill(0), Z = new Array(9).fill(0);
        for (let j = 1; j <= 8; j++) {
            const u = s4[j].lam - s4[j].node;
            const w = s4[j].node - 168.8112 * DEG;
            const r = s4[j].r;
            X[j] = r * (cos(u) * cos(w) - sin(u) * cos(s4[j].gam) * sin(w));
            Y[j] = r * (sin(u) * cos(w) * cos(s4[j].gam) + cos(u) * sin(w));
            Z[j] = r * sin(u) * sin(s4[j].gam);
        }
        Z[0] = 1;

        const sLam0 = sin(lam0), cLam0 = cos(lam0);
        const sBeta0 = sin(beta0), cBeta0 = cos(beta0);
        const A_ = new Array(9).fill(0), B_ = new Array(9).fill(0), C_ = new Array(9).fill(0);
        for (let j = 0; j <= 8; j++) {
            let a = X[j];
            let bb = q.c1 * Y[j] - q.s1 * Z[j];
            const c = q.s1 * Y[j] + q.c1 * Z[j];
            const a0 = q.c2 * a - q.s2 * bb;
            bb = q.s2 * a + q.c2 * bb;
            a = a0;
            A_[j] = a * sLam0 - bb * cLam0;
            bb = a * cLam0 + bb * sLam0;
            B_[j] = bb * cBeta0 + c * sBeta0;
            C_[j] = c * cBeta0 - bb * sBeta0;
        }

        const D = Math.atan2(A_[0], C_[0]);
        const sD = sin(D), cD = cos(D);
        const out = [];
        for (let j = 1; j <= 8; j++) {
            let x = A_[j] * cD - C_[j] * sD;
            const y = A_[j] * sD + C_[j] * cD;
            const z = B_[j];
            const dOverR = x / s4[j].r;
            x += Math.abs(z) / K_LIGHT[j] * Math.sqrt(Math.max(0, 1 - dOverR * dOverR));
            const W = distAU / (distAU + z / RS_PER_AU_CH46);
            out.push({ x: x * W, y: y * W, z });
        }
        return out;
    }

    /**
     * The eight satellites in ephem's frame: x east on the sky, y south, z towards the
     * Earth, in Saturn equatorial radii.
     *
     * Meeus's own pair is ring-aligned, not north/east aligned: his X runs along the ring's
     * node line, at position angle P - 90, and his Y along the ring pole's sky projection,
     * at P. ch. 46's D = atan2(A0, C0) is what puts them there, A0 and C0 being the pole's
     * own components — so it is the position angle of the pole that turns the pair back to
     * north and east, and that is the same number the page draws the ring ellipse at.
     */
    function moons(utcMs) {
        const j = earthToSaturn(utcMs);
        const jde = jdOf(utcMs) - j.dist * LIGHT_DAYS_PER_AU;
        const raw = meeusPositions(jde, j.dist, j.u);
        const pa = positionAngleOfDate(pole(utcMs), j.u, utcMs) * DEG;
        const cpa = cos(pa), spa = sin(pa);

        return MOONS.map((name, i) => {
            // Meeus's X along the node line, his Y along the pole's projection. A step
            // `along` in X lies at P - 90 on the sky and a step `across` in Y at P, so
            // east = -along cos P + across sin P and north = along sin P + across cos P.
            const xEast = -raw[i].x * cpa + raw[i].y * spa;
            const ySouth = -(raw[i].x * spa + raw[i].y * cpa);
            return {
                name,
                x: xEast, y: ySouth, z: -raw[i].z,
                r: Math.hypot(xEast, ySouth),
                west: xEast < 0,
            };
        });
    }

    function moon(utcMs, name) {
        const i = MOONS.indexOf(name);
        if (i < 0) throw new Error('nova_saturn: unknown moon ' + JSON.stringify(name));
        return moons(utcMs)[i];
    }

    // ------------------------------------------------------------------ elongations

    /**
     * Iapetus's greatest western elongations between two instants: the moments it stands
     * furthest west of Saturn on the sky, 79.33 days apart. Those are the nights its bright
     * trailing hemisphere is turned towards the Earth and it is at its brightest, which is
     * the one thing about the satellites worth a Calendar row.
     *
     * Source for the rule, quoted in full in the header of tools/test_saturn.cjs: the BAA's
     * "Iapetus is brightest when on the western side of Saturn and faintest when on the
     * eastern side" (Abel & Lawrence, Observer's Challenge: Saturn vs Pluto, 2019) and the
     * JPL Cassini outreach guide's "a bright magnitude 10.1 at western elongation and a
     * faint 11.9 magnitude at eastern elongation" with "the light trailing side of tidally
     * locked Iapetus when it is at western elongation" (Jones).
     *
     * The scan is 2 days, a fortieth of the period, and the extremum is then closed on by
     * golden section, as nova_jupiter.js closes on its own.
     */
    function elongations(startUtcMs, endUtcMs) {
        const SAMPLES = 2 * 24 * 3600000;
        const west = t => -moons(t)[7].x;
        const out = [];
        let t0 = startUtcMs - SAMPLES;
        let w0 = west(t0), t1 = startUtcMs, w1 = west(t1);
        while (t1 < endUtcMs + SAMPLES) {
            const t2 = t1 + SAMPLES, w2 = west(t2);
            if (w1 > w0 && w1 >= w2) {
                let lo = t0, hi = t2;
                for (let n = 0; n < 60 && hi - lo > 1000; n++) {
                    const m1 = hi - (hi - lo) * 0.618034, m2 = lo + (hi - lo) * 0.618034;
                    if (west(m1) > west(m2)) hi = m2; else lo = m1;
                }
                const t = Math.round((lo + hi) / 2);
                if (t >= startUtcMs && t < endUtcMs) {
                    const m = moons(t)[7];
                    out.push({ utcMs: t, moon: 'Iapetus', side: 'west', distanceRs: m.r });
                }
            }
            t0 = t1; w0 = w1; t1 = t2; w1 = w2;
        }
        out.sort((a, b) => a.utcMs - b.utcMs);
        return out;
    }

    return {
        MOONS, RING_EDGES, ringPlaneElements,
        geometry, moons, moon, elongations,
        pole, positionAngle, positionAngleOfDate, eclipticPrecess,
    };
});
