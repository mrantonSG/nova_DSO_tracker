/*
 * nova_jupiter.js — Jupiter as a globe and as a system of moons: the central meridian
 * in System I, II and III, the planetocentric latitude of the Earth and of the Sun,
 * the position angle of the axis, where the four Galilean satellites are on the sky, and
 * when they transit the disc, are occulted by it, are eclipsed by the planet's shadow, and
 * cast their own shadows on it.
 *
 * Maths only: which of it to show, and when, is the page's business.
 *
 * Accuracy is checked with tools/test_jupiter.cjs (fixture
 * tests/fixtures/jupiter_reference.json, built by tools/gen_jupiter_fixture.py) against
 * ephem's Bureau des Longitudes (BDL) Galilean theory and against the IAU rotation model
 * of Jupiter applied to ephem's positions. Any change to this file must keep that check
 * green.
 *
 * Pass marks (Rj = Jupiter's equatorial radius), measured over the 680 instants of
 * tests/fixtures/jupiter_reference.json, which run 2024 to 2040-01-14, the range over
 * which ephem's BDL data is valid — see the note below. Each is the worst case measured
 * on that fixture, with the mark set just above it:
 *   System III       0.05 deg (0.0310)      De, Ds      0.008 deg (0.0022, 0.0027)
 *   polePA           0.02 deg (0.0065)      sunPA       0.03 deg (0.0105) at phase
 *                                                        angle >= 6 deg; 1.2 deg (0.9095)
 *                                                        over all instants
 *   Systems I and II 0.001 deg (0.000001)   sky tilt    0.03 deg (0.0205)
 *   diameter         0.06 arcsec (0.0339)   illumination 0.005 per cent (0.0012)
 *   distances        0.006 AU (0.0030, 0.0028); light time is distance / c, exactly
 *   against Horizons: System III 0.05 deg (0.0283), De 0.008 (0.0025), Ds 0.008 (0.0029)
 *   moon position   Io 0.012, Europa 0.03, Ganymede 0.04, Callisto 0.075 Rj
 *                    (worst measured 0.0099, 0.0263, 0.0354, 0.0667)
 *   event instant   Io 3, Europa 3, Ganymede 4, Callisto 9 minutes
 *                    (worst measured 0.66, 2.12, 3.38, 6.08 over the fixture's 3413
 *                     events in 28 windows - transits, occultations, eclipses and
 *                     shadow transits alike - every one found and the counts equal,
 *                     moon for moon and kind for kind)
 * The event marks are the moon position marks above divided by the moons' sky speeds,
 * 0.0145, 0.0116, 0.0092 and 0.0069 Rj a minute: an event instant is the place difference
 * turned into time. That division is what makes it the sharpest test of a satellite place
 * in this file, and it is why the outer two, whose positions barely fit their own marks,
 * still land well inside these.
 * events() costs 127 ms for a 31-day window (about 1.5 s for a year) on this machine,
 * scanning at 10 minutes and bisecting to a second, with the four kinds of phenomenon
 * sharing the boundary crossings they have in common; the page's budget is measured
 * against that.
 * The two sunPA marks are the conditioned and the tripwire form of one quantity, not a
 * fudge: see the sunPA note under Conventions and the header of tools/test_jupiter.cjs,
 * which carries the measured band table.
 * The outer marks are what the abridged E5 series can actually deliver, not what one
 * would like: Meeus ch. 44 prints 43 and 50 terms for Ganymede's and Callisto's longitudes
 * out of the several hundred of the full Lieske (1998) theory, and the full table was not
 * obtainable when this was written. The residual is almost entirely tangential — a
 * longitude error: taking out the best-fit rotation of the sky plane moves it by under
 * 0.0001 Rj — and it is measured, not assumed. An earlier, 124-instant sample gave 0.0095,
 * 0.0171, 0.0364 and 0.0538 Rj, and the marks were first set from it; sampling densely
 * enough to reach the outer moons' worst phases is what moved them here.
 *
 * Theory
 *   - Jupiter's place and the frame: NovaPlanets.position('jupiter') (astrometric J2000,
 *     light-time corrected) and NovaAstro.frameFor(). The pole is taken in the ICRF against
 *     Jupiter's place turned to the mean equator of date, which is what reproduces ephem's
 *     sky orientation (to 0.008 deg); the apparent place would put the whole system 0.2 deg
 *     out, and a J2000 place 0.2 deg out the other way.
 *   - System III: the IAU/WGCCRE rotation model. alpha0 = 268.056595 - 0.006499 T,
 *     delta0 = 64.495303 + 0.002413 T, W = 284.95 + 870.5360000 d (d in days TT from
 *     J2000). Source: Archinal et al., "Report of the IAU Working Group on Cartographic
 *     Coordinates and Rotational Elements", Celest. Mech. Dyn. Astr. 130:22 (2018), and the
 *     SPICE kernel pck00011.tpc (BODY599_POLE_RA, BODY599_POLE_DEC, BODY599_PM), which is
 *     where the 870.5360000 rate is taken; the report text prints 870.5366420. The pole's
 *     nutation terms (Ja..Je, under 0.0022 deg in RA) are the ones the kernel carries, and
 *     leaving them out is what puts a 0.03 deg residual into System III against Horizons,
 *     so they are in. W is evaluated at the light-time-corrected instant and the Earth's
 *     direction in the body frame is taken off it: the central meridian is the sub-observer
 *     longitude, not W itself, and the two drift apart by several degrees over one orbit of
 *     Jupiter.
 *   - System I and II: Meeus, Astronomical Formulae for Calculators, 2nd ed., ch. 35-36,
 *     the model libastro's meeus_jupiter() (jupmoon.c) uses, which is where the rates
 *     877.8169088 and 870.1869088 deg/day and the epochs 268.28 and 290.28 come from. The
 *     form already carries the light time (the -Delta/173 term) and the Earth-direction
 *     term (psi - B). libastro's own comment is "always use Meeus for cml"; ephem's
 *     cmlI/cmlII is the same low-accuracy model, so it is a cross-check on the
 *     transcription rather than an independent reference — the harness states its mark.
 *   - The Galilean satellites: Meeus, Astronomical Algorithms, 2nd ed., ch. 44 — the
 *     abridged Lieske E5 theory, every printed periodic term in longitude, latitude and
 *     radius. Numeric coefficients transcribed from PyMeeus 0.5.12
 *     (pymeeus/JupiterMoons.py); the code here is written from them, not copied, and
 *     PyMeeus is LGPL. Validated against ephem's BDL, the high-accuracy numerical theory;
 *     that comparison is the authority for the pass marks above.
 *
 * Three notes on the references, all of which the parity check depends on:
 *   - Jupiter's radius. Meeus's mean distances a_i are in units of a Jupiter equatorial
 *     radius of about 71416 km; libastro divides the BDL positions by JUPRAU =
 *     0.0004769108 AU = 71344.84 km. The two differ by 0.10 per cent — a pure unit
 *     convention, which shows up as a radial offset and nothing else. Converting Meeus's
 *     a_i with the fitted factor returns 421760, 671064, 1070404 and 1882718 km, against
 *     Lieske E5's 421800, 671100, 1070400 and 1882700: the identification is exact to
 *     0.01 per cent, so this is a known convention and not a model error. The parity
 *     harness puts both sides on the same radius (R_J = 71416 km) before comparing.
 *   - The Akiyama corrections. Two coefficients in Meeus's ch. 44 periodic terms are wrong
 *     in print: +0.01294 sin(pi_3 - pi_4) in Europa's longitude series and
 *     -0.00223 sin(l_3 - l_4) in Callisto's. They are community errata and are NOT in
 *     Meeus's official errata list for the 2nd edition; the source used here is the
 *     'KA fix' markers in Bill Gray's jsats.cpp (28 Sep 2002 note, Kazumi Akiyama), which
 *     bound the effect at 300 km worst case for Europa and 3 km for Callisto. The values
 *     in this file are the corrected ones, and are what PyMeeus carries as well.
 *   - ephem's Galilean moons are high-accuracy BDL data compiled into the extension, but
 *     libastro selects its 2040 dataset for JD < 2466520.5 when that dataset runs out
 *     about a year early, so the moons are garbage after 2040-01-15. Nothing here is
 *     validated beyond 2040-01-14.
 *
 * Conventions
 *   - Degrees everywhere. The central meridian longitudes I, II and III are 0..360 and
 *     planetographic: positive westward, increasing with the body's rotation. That is what
 *     observers quote (JUPOS, Sky & Telescope) and what JPL Horizons prints as ObsSub-LON.
 *   - de, ds: planetocentric latitude of the Earth and of the Sun, north-positive. Over
 *     2024-2040 De stays inside +-3.44 deg and Ds inside +-3.12 deg; Horizons prints the
 *     same two points planetodetically, which is 0.23 deg away at De = 3.4 deg.
 *   - polePA: position angle of Jupiter's north pole, from celestial north through east.
 *   - sunPA: position angle of the Sun's direction as seen from Jupiter, which is the
 *     lighting direction on the disc. Both directions are turned to the equator of date
 *     together (see polePA). Caution: Jupiter's phase angle never exceeds 11.5 deg, so the
 *     Sun is always within 11.5 deg of antipodal to Jupiter's own geocentric direction, and
 *     at conjunction and opposition — where the phase angle passes through zero — the
 *     position angle of one from the other is degenerate. There the ordinary 0.007 deg
 *     difference between ephem's places and NovaPlanets' moves it by up to 19 deg, which is
 *     what the two-band mark on it in tools/test_jupiter.cjs is for. The quantity is still
 *     worth having: it is what caught the Sun rebuilt 279 deg out in longitude.
 *   - moons(): x east on the sky, y south, z towards the Earth, in Jupiter equatorial
 *     radii. This is ephem's own convention, so the parity check is a direct comparison.
 *     The page turns it into whatever view the observer's telescope gives.
 *   - Times are UTC. Positions use UTC as TT, as in nova_astro.js; the rotation angles are
 *     the one place where the difference shows, so System III is evaluated on TT and the
 *     AFC Systems I and II, which are written on UT, are not (TT_MINUS_UTC_DAYS).
 *
 * geometry(utcMs) -> {
 *     cm: { I, II, III },          central meridian longitudes, degrees 0..360
 *     de, ds,                      planetocentric latitude of the Earth, of the Sun
 *     polePA,                      position angle of the north pole
 *     diameterArcsec,              apparent equatorial diameter
 *     distanceAU, sunDistAU,       from the Earth, from the Sun
 *     phaseAngleDeg,               Sun-Jupiter-Earth
 *     lightTimeDays,
 *     sunPA,                       position angle of the Sun's direction, degrees
 *   }
 * moons(utcMs) -> [{ name, x, y, z, rj }] in the order MOONS
 * moon(utcMs, name) -> one entry of the above; an unknown name throws
 *
 * Depends on the globals NovaAstro (src/nova_astro.js) and NovaPlanets
 * (src/nova_planets.js), both loaded as plain scripts first.
 * No DOM, no network: the module runs unchanged in Node.
 */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.NovaJupiter = factory();
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const PI = Math.PI, TAU = 2 * PI;
    const DEG = PI / 180, HOUR = PI / 12;
    const DAY_MS = 86400000;
    const AU_KM = 149597870.7;
    const LIGHT_DAYS_PER_AU = 0.0057755183;

    // TT - UTC. TT = TAI + 32.184 s and TAI - UTC = 37 s since 2017-01-01, so 69.184 s.
    // pck00011.tpc, "Time systems and reference frames": the rotation formulas take their
    // independent variable in TDB (TT is within 2 ms of it), and the epoch is J2000. So
    // this belongs in W - where it is 0.70 deg of System III - and in the pole's T, where
    // it is 2e-8 centuries. AFC's Systems I and II are written on UT and libastro runs them
    // that way, so they keep the UT instant instead.
    const TT_MINUS_UTC_MS = 69184;
    const TT_MINUS_UTC_DAYS = TT_MINUS_UTC_MS / DAY_MS;
    const EPS_J2000 = 23.4392911 * DEG;

    // IAU/WGCCRE pole of Jupiter (System III), J2000. SPICE pck00011.tpc BODY599_*.
    // The kernel's prose example writes the prime meridian rate as 870.5366420; its own
    // BODY599_PM data value, which is what SPICE and Horizons use, is 870.5360000, and the
    // two differ by 0.00064 deg/day - 3.75 deg across the 16 years this fixture covers. The
    // Horizons check shows no secular drift at all, which fixes the data value as the one
    // in use here.
    const POLE_RA0 = 268.056595, POLE_RA_RATE = -0.006499;    // degrees, per century
    const POLE_DEC0 = 64.495303, POLE_DEC_RATE = 0.002413;
    const W0 = 284.95, W_RATE = 870.5360000;                   // degrees, per day

    // Jupiter's pole nutation, same kernel. The kernel's arrays run J1..J10 then Ja..Je;
    // every amplitude in the first ten slots is zero, so only these five do anything.
    // Amplitudes are degrees, and the angles are degrees, linear in T, centuries from J2000.
    const NUT_RA = [0.000117, 0.000938, 0.001432, 0.000030, 0.002150];
    const NUT_DEC = [0.000050, 0.000404, 0.000617, -0.000013, 0.000926];
    const NUT_ANGLES = [
        [99.360714, 4850.4046],     // Ja
        [175.895369, 1191.9605],    // Jb
        [300.323162, 262.5475],     // Jc
        [114.012305, 6070.2476],    // Jd
        [49.511251, 64.3000],       // Je
    ];

    // Meeus AFC ch. 35-36, as libastro's meeus_jupiter(): System I and II, degrees.
    const SYS1_EPOCH = 268.28, SYS1_RATE = 877.8169088;
    const SYS2_EPOCH = 290.28, SYS2_RATE = 870.1869088;

    // The time base those AFC formulae are written for, and the one libastro hands them:
    // days from 1900 January 0.5. libastro astro.h defines MJD0 as 2415020.0 and takes
    // JD = Mjd + MJD0, so libastro's "Mjd" is JD - 2415020, not the standard MJD. See afc().
    const AFC_EPOCH = 2415020.0;

    const MOONS = Object.freeze(['io', 'europa', 'ganymede', 'callisto']);

    // Meeus ch. 44: mean distances in Jupiter equatorial radii, and the epoch of the series.
    const A = [5.90569, 9.39657, 14.98832, 26.36273];
    const E5_EPOCH_JD = 2443000.5;

    const sin = Math.sin, cos = Math.cos;
    const mod = (a, n) => ((a % n) + n) % n;
    const clamp1 = x => (x > 1 ? 1 : x < -1 ? -1 : x);
    const centuries = utcMs => (utcMs / DAY_MS + 2440587.5 - 2451545.0) / 36525;
    const S = d => sin(d * DEG);                               // sine of an argument in degrees

    // NovaAstro and NovaPlanets are globals from scripts loaded before this one; looked
    // up at call time, as nova_moon.js does it.
    function engine() {
        const P = typeof NovaPlanets !== 'undefined' ? NovaPlanets : null;
        if (!P) throw new Error('nova_jupiter: NovaPlanets is not loaded (src/nova_planets.js must come first)');
        const A_ = typeof NovaAstro !== 'undefined' ? NovaAstro : null;
        if (!A_) throw new Error('nova_jupiter: NovaAstro is not loaded (src/nova_astro.js must come first)');
        return { planets: P, astro: A_ };
    }

    const norm = v => Math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
    const unit = v => { const n = norm(v); return [v[0] / n, v[1] / n, v[2] / n]; };
    const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
    /** Row-major 3x3 matrix times a vector. */
    const apply = (M, v) => [
        M[0] * v[0] + M[1] * v[1] + M[2] * v[2],
        M[3] * v[0] + M[4] * v[1] + M[5] * v[2],
        M[6] * v[0] + M[7] * v[1] + M[8] * v[2],
    ];

    // ------------------------------------------------------------------ directions

    /** Unit vector Earth -> Jupiter, ICRF/J2000 astrometric, and the distances in AU. */
    function earthToJupiter(utcMs) {
        const p = engine().planets.position('jupiter', utcMs);
        const ra = p.raHours * HOUR, dec = p.decDeg * DEG, cd = cos(dec);
        return { u: [cd * cos(ra), cd * sin(ra), sin(dec)], dist: p.distAU, sunDist: p.sunDistAU };
    }

    /**
     * Unit vector Earth -> Sun, ICRF/J2000, and the distance in AU.
     *
     * sunPosition() offers the Sun's geometric longitude on the mean equinox of date
     * (trueLon) as well as the apparent one, and this takes the geometric: the vector
     * wanted here is the Earth-Sun direction itself, and the apparent place carries the
     * 20.5" of annual aberration, which is an observing artefact rather than a shift of the
     * Sun. Taking off the precession in longitude then reaches J2000, and the obliquity of
     * J2000 turns the ecliptic into the equator. The Sun's ecliptic latitude is under 1"
     * (the Earth-Moon barycentre offset) and is taken as zero.
     *
     * Watch the units: sunPosition() returns trueLon in RADIANS while the precession series
     * below is in degrees, so prec is the only term that carries DEG. Reading the whole sum
     * as degrees is a 279 deg error in the Sun's longitude, which is what the Horizons
     * sub-solar check caught.
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
     * Jupiter's pole direction, ICRF/J2000, from the IAU/WGCCRE elements:
     *
     *   alpha = 268.056595 - 0.006499 T + sum A_i sin(J_i)
     *   delta =  64.495303 + 0.002413 T + sum B_i cos(J_i)
     *
     * The five nutation terms are the ones the kernel actually carries, in the slots for
     * Ja..Je (the first ten slots, for J1..J10, are all zero). T is centuries past J2000
     * TDB; angles J1..J8 are for the satellites and are not used here. The pole wanders by
     * up to 0.005 deg in RA and 0.002 deg in DEC, which is the size of the residual the
     * Horizons check was showing before these were added, and it is real in the kernel, so
     * unlike Mars it is not an oversight.
     */
    function pole(utcMs) {
        const T = centuries(utcMs + TT_MINUS_UTC_MS);
        let ra = POLE_RA0 + POLE_RA_RATE * T, dec = POLE_DEC0 + POLE_DEC_RATE * T;
        for (let i = 0; i < NUT_ANGLES.length; i++) {
            const [a0, a1] = NUT_ANGLES[i];
            const arg = (a0 + a1 * T) * DEG;
            ra += NUT_RA[i] * sin(arg);
            dec += NUT_DEC[i] * cos(arg);
        }
        ra *= DEG; dec *= DEG;
        return [cos(dec) * cos(ra), cos(dec) * sin(ra), sin(dec)];
    }

    /**
     * Position angle of Jupiter's north pole: the ICRF pole against Jupiter's place, both
     * turned to the equator of date. Mounting convention, north through east. Turning both
     * is the point: a position angle is measured from the pole of the frame it is written
     * in, so mixing the two frames - the place of date against the pole of J2000 - is up to
     * 0.2 deg out, which is what this did before, and the sub-solar direction below was
     * left in J2000 while this was not.
     */
    function polePA(uJ, utcMs) {
        return positionAngleOfDate(pole(utcMs), uJ, utcMs);
    }

    /**
     * Position angle of a J2000 direction v against Jupiter's place uJ, both turned to the
     * equator of date: what an observer sees against celestial north.
     */
    function positionAngleOfDate(v, uJ, utcMs) {
        const M = engine().astro.frameFor(utcMs).M;
        return positionAngle(apply(M, v), apply(M, uJ));
    }

    /**
     * The angle the four satellites' sky plane is turned through, which is the position
     * angle of the pole taken against Jupiter's place in the *J2000* frame — not the place
     * of date. This is libastro's frame: jupmoon.c sets md[0].ra = jop->s_ra, the J2000
     * place, and ephem's satellite x/y are offsets in that frame. Taking the angle of date
     * instead turns the whole pattern by 0.085 deg in 2024 growing to 0.143 deg by 2036 and
     * doubles the residual against ephem.
     */
    function skyPA(uJ) {
        const pr = POLE_RA0 * DEG, pd = POLE_DEC0 * DEG;
        const poleJ2000 = [cos(pd) * cos(pr), cos(pd) * sin(pr), sin(pd)];
        return positionAngle(poleJ2000, uJ);
    }

    /** Position angle of a direction v (J2000) against Jupiter (uJ, J2000), north through east. */
    function positionAngle(v, uJ) {
        const raJ = Math.atan2(uJ[1], uJ[0]), decJ = Math.asin(clamp1(uJ[2]));
        const ra = Math.atan2(v[1], v[0]), dec = Math.asin(clamp1(v[2]));
        return mod(Math.atan2(cos(dec) * sin(ra - raJ),
            sin(dec) * cos(decJ) - cos(dec) * sin(decJ) * cos(ra - raJ)) / DEG, 360);
    }

    // ------------------------------------------------------------------ System I / II

    /**
     * Meeus AFC ch. 35-36: the classic Jupiter model, run exactly as libastro's
     * meeus_jupiter() runs it. Returns the Earth-Jupiter distance, the phase angle and
     * Jupiter's equation of the centre, in degrees — the last two are what the System I
     * and II longitudes are corrected by.
     *
     * Watch the units: B is written as 5.552 sin N + 0.167 sin 2N and read straight into
     * a longitude in degrees, so it is already degrees. Treating it as radians and
     * converting puts a 49 deg error into System I.
     *
     * `d` is the argument the AFC formulae are written for: days from 1900 January 0.5,
     * i.e. JD - 2415020.0. That is not the standard Modified Julian Date, but it is what
     * libastro means by the time argument it hands meeus_jupiter(): astro.h line 57 reads
     * `#define MJD0 2415020.0` and jupmoon.c does `JD = Mjd + MJD0`, so libastro's "Mjd"
     * throughout is Jd - 2415020. Passing a real MJD here instead shifts theta by 15019.5
     * days, which is a constant 72.43 deg error in System I and 46.33 deg in System II (and
     * moves the Earth-Jupiter distance by up to 0.94 AU), because J, M, N and V all step
     * with d. Verified: with this base, cmlI and cmlII agree with ephem's to 0.000 deg over
     * 2024-2039 and this distance to 0.003 AU - ephem's own cmlI/cmlII *are* this function.
     */
    function afc(d) {
        // Degrees throughout, exactly as libastro writes it. A and B are perturbations in
        // degrees added to J, so converting any one of them to radians and not the others
        // gives a meaningless K and a wrong Earth-Jupiter distance.
        const cs = x => cos(x * DEG);
        const V = 134.63 + 0.00111587 * d;
        const M = 358.47583 + 0.98560003 * d;
        const N = 225.32833 + 0.0830853 * d + 0.33 * S(V);
        const J = 221.647 + 0.9025179 * d - 0.33 * S(V);
        const Aa = 1.916 * S(M) + 0.02 * S(2 * M);
        const Bb = 5.552 * S(N) + 0.167 * S(2 * N);
        const K = J + Aa - Bb;
        const R = 1.00014 - 0.01672 * cs(M) - 0.00014 * cs(2 * M);
        const r = 5.20867 - 0.25192 * cs(N) - 0.00610 * cs(2 * N);
        const del = Math.sqrt(R * R + r * r - 2 * R * r * cs(K));
        return { del, phase: Math.asin(clamp1(R / del * S(K))) / DEG, centre: Bb };
    }

    /**
     * System I, II and III longitudes of the central meridian, degrees 0..360, positive
     * westward — the observer's convention, and Horizons' for Jupiter.
     */
    function centralMeridians(utcMs, uJ, distAU) {
        const jd = utcMs / DAY_MS + 2440587.5;

        // System I and II: Meeus AFC, which carries the light time and the Earth's
        // direction already. AFC's recipes are written on UT, as libastro runs them, and
        // this is the UT instant the caller gives us: no TT here, and adding one would
        // break the agreement with ephem's cmlI/cmlII (69.184 s is 0.70 deg of System I).
        const dd = jd - AFC_EPOCH;
        const a = afc(dd);
        const solc = dd - a.del / 173;
        const comm = a.phase - a.centre;
        const I = mod(SYS1_EPOCH + SYS1_RATE * solc + comm, 360);
        const II = mod(SYS2_EPOCH + SYS2_RATE * solc + comm, 360);

        // System III: W at the light-time-corrected instant, less the Earth's longitude in
        // the body frame. The body frame is the IAU one: z along the pole, x at the node
        // of the J2000 equator on the body equator, turned by W. Unlike AFC, the WGCCRE
        // model is defined with d in TT, so the 69.184 s of TT - UTC belongs here.
        const d = (jd + TT_MINUS_UTC_DAYS - 2451545.0) - distAU * LIGHT_DAYS_PER_AU;
        const W = mod(W0 + W_RATE * d, 360);
        const z = pole(utcMs);
        // The node of the body equator on the J2000 equator, which is (-sin alpha, cos
        // alpha, 0) for the pole's own right ascension. Taken from the pole itself rather
        // than from POLE_RA0, so that the nutation cannot tip the frame's x axis off the
        // node it is defined to start at.
        const node = unit(cross([0, 0, 1], z));
        const perp = unit(cross(z, node));
        const w = W * DEG;
        const xb = [cos(w) * node[0] + sin(w) * perp[0], cos(w) * node[1] + sin(w) * perp[1],
            cos(w) * node[2] + sin(w) * perp[2]];
        const yb = cross(z, xb);
        const toEarth = [-uJ[0], -uJ[1], -uJ[2]];
        // atan2(yb, xb) is the Earth's longitude in the body frame measured *eastward* from
        // the prime meridian, so it is already the sub-Earth point's east longitude and the
        // central meridian is its negative. Writing W - this (which is what this did) puts
        // an extra whole rotation into the answer: it is 267 deg out here, and not even
        // constant, since W runs. Compared against Horizons it shows up as a residual whose
        // scatter tracks W rather than staying flat.
        const eastLon = Math.atan2(dot(toEarth, yb), dot(toEarth, xb)) / DEG;
        return { I, II, III: mod(-eastLon, 360) };
    }

    // ------------------------------------------------------------------ Galilean satellites

    /**
     * Meeus ch. 44, the abridged Lieske E5 theory: each satellite's place in Jupiter's
     * equatorial frame, in Jupiter equatorial radii — x west, y north, z away from the
     * Earth, the convention the series are printed in. Every printed term is here.
     */
    function galilean(utcMs, distAU) {
        const t = (utcMs / DAY_MS + 2440587.5) - E5_EPOCH_JD - distAU * LIGHT_DAYS_PER_AU;

        const l1 = 106.07719 + 203.488955790 * t, l2 = 175.73161 + 101.374724735 * t;
        const l3 = 120.55883 + 50.317609207 * t, l4 = 84.44459 + 21.571071177 * t;
        const p1 = 97.0881 + 0.16138586 * t, p2 = 154.8663 + 0.04726307 * t;
        const p3 = 188.1840 + 0.00712734 * t, p4 = 335.2868 + 0.00184000 * t;
        const o1 = 312.3346 - 0.13279386 * t, o2 = 100.4411 - 0.03263064 * t;
        const o3 = 119.1942 - 0.00717703 * t, o4 = 322.6186 - 0.00175934 * t;
        const gam = 0.33033 * S(163.679 + 0.0010512 * t) + 0.03439 * S(34.486 - 0.0161731 * t);
        const phi = 199.6766 + 0.17379190 * t;      // libration of the inner satellites
        const psi = 316.5182 - 0.00000208 * t;      // node of Jupiter's equator on the ecliptic
        const G = 30.23756 + 0.0830925701 * t + gam;    // mean anomaly of Jupiter
        const Gs = 31.97853 + 0.0334597339 * t;         // mean anomaly of Saturn
        const PER = 13.469942;                          // longitude of Jupiter's perihelion

        let sum1 = 0;
        sum1 += 0.47259 * S(2 * (l1 - l2));
        sum1 -= 0.03478 * S(p3 - p4);
        sum1 += 0.01081 * S(l2 - 2 * l3 + p3);
        sum1 += 0.00738 * S(phi);
        sum1 += 0.00713 * S(l2 - 2 * l3 + p2);
        sum1 -= 0.00674 * S(p1 + p3 - 2 * PER - 2 * G);
        sum1 += 0.00666 * S(l2 - 2 * l3 + p4);
        sum1 += 0.00445 * S(l1 - p3);
        sum1 -= 0.00354 * S(l1 - l2);
        sum1 -= 0.00317 * S(2 * psi - 2 * PER);
        sum1 += 0.00265 * S(l1 - p4);
        sum1 -= 0.00186 * S(G);
        sum1 += 0.00162 * S(p2 - p3);
        sum1 += 0.00158 * S(4 * (l1 - l2));
        sum1 -= 0.00155 * S(l1 - l3);
        sum1 -= 0.00138 * S(psi + o3 - 2 * PER - 2 * G);
        sum1 -= 0.00115 * S(2 * (l1 - 2 * l2 + o2));
        sum1 += 0.00089 * S(p2 - p4);
        sum1 += 0.00085 * S(l1 + p3 - 2 * PER - 2 * G);
        sum1 += 0.00083 * S(o2 - o3);
        sum1 += 0.00053 * S(psi - o2);

        let sum2 = 0;
        sum2 += 1.06476 * S(2 * (l2 - l3));
        sum2 += 0.04256 * S(l1 - 2 * l2 + p3);
        sum2 += 0.03581 * S(l2 - p3);
        sum2 += 0.02395 * S(l1 - 2 * l2 + p4);
        sum2 += 0.01984 * S(l2 - p4);
        sum2 -= 0.01778 * S(phi);
        sum2 += 0.01654 * S(l2 - p2);
        sum2 += 0.01334 * S(l2 - 2 * l3 + p2);
        sum2 += 0.01294 * S(p3 - p4);                       // Akiyama-corrected
        sum2 -= 0.01142 * S(l2 - l3);
        sum2 -= 0.01057 * S(G);
        sum2 -= 0.00775 * S(2 * (psi - PER));
        sum2 += 0.00524 * S(2 * (l1 - l2));
        sum2 -= 0.00460 * S(l1 - l3);
        sum2 += 0.00316 * S(psi - 2 * G + o3 - 2 * PER);
        sum2 -= 0.00203 * S(p1 + p3 - 2 * PER - 2 * G);
        sum2 += 0.00146 * S(psi - o3);
        sum2 -= 0.00145 * S(2 * G);
        sum2 += 0.00125 * S(psi - o4);
        sum2 -= 0.00115 * S(l1 - 2 * l3 + p3);
        sum2 -= 0.00094 * S(2 * (l2 - o2));
        sum2 += 0.00086 * S(2 * (l1 - 2 * l2 + o2));
        sum2 -= 0.00086 * S(5 * Gs - 2 * G + 52.225);
        sum2 -= 0.00078 * S(l2 - l4);
        sum2 -= 0.00064 * S(3 * l3 - 7 * l4 + 4 * p4);
        sum2 += 0.00064 * S(p1 - p4);
        sum2 -= 0.00063 * S(l1 - 2 * l3 + p4);
        sum2 += 0.00058 * S(o3 - o4);
        sum2 += 0.00056 * S(2 * (psi - PER - G));
        sum2 += 0.00056 * S(2 * (l2 - l4));
        sum2 += 0.00055 * S(2 * (l1 - l3));
        sum2 += 0.00052 * S(3 * l3 - 7 * l4 + p3 + 3 * p4);
        sum2 -= 0.00043 * S(l1 - p3);
        sum2 += 0.00041 * S(5 * (l2 - l3));
        sum2 += 0.00041 * S(p4 - PER);
        sum2 += 0.00032 * S(o2 - o3);
        sum2 += 0.00032 * S(2 * (l3 - G - PER));

        let sum3 = 0;
        sum3 += 0.16490 * S(l3 - p3);
        sum3 += 0.09081 * S(l3 - p4);
        sum3 -= 0.06907 * S(l2 - l3);
        sum3 += 0.03784 * S(p3 - p4);
        sum3 += 0.01846 * S(2 * (l3 - l4));
        sum3 -= 0.01340 * S(G);
        sum3 -= 0.01014 * S(2 * (psi - PER));
        sum3 += 0.00704 * S(l2 - 2 * l3 + p3);
        sum3 -= 0.00620 * S(l2 - 2 * l3 + p2);
        sum3 -= 0.00541 * S(l3 - l4);
        sum3 += 0.00381 * S(l2 - 2 * l3 + p4);
        sum3 += 0.00235 * S(psi - o3);
        sum3 += 0.00198 * S(psi - o4);
        sum3 += 0.00176 * S(phi);
        sum3 += 0.00130 * S(3 * (l3 - l4));
        sum3 += 0.00125 * S(l1 - l3);
        sum3 -= 0.00119 * S(5 * Gs - 2 * G + 52.225);
        sum3 += 0.00109 * S(l1 - l2);
        sum3 -= 0.00100 * S(3 * l3 - 7 * l4 + 4 * p4);
        sum3 += 0.00091 * S(o3 - o4);
        sum3 += 0.00080 * S(3 * l3 - 7 * l4 + p3 + 3 * p4);
        sum3 -= 0.00075 * S(2 * l2 - 3 * l3 + p3);
        sum3 += 0.00072 * S(p1 + p3 - 2 * PER - 2 * G);
        sum3 += 0.00069 * S(p4 - PER);
        sum3 -= 0.00058 * S(2 * l3 - 3 * l4 + p4);
        sum3 -= 0.00057 * S(l3 - 2 * l4 + p4);
        sum3 += 0.00056 * S(l3 + p3 - 2 * PER - 2 * G);
        sum3 -= 0.00052 * S(l2 - 2 * l3 + p1);
        sum3 -= 0.00050 * S(p2 - p3);
        sum3 += 0.00048 * S(l3 - 2 * l4 + p3);
        sum3 -= 0.00045 * S(2 * l2 - 3 * l3 + p4);
        sum3 -= 0.00041 * S(p2 - p4);
        sum3 -= 0.00038 * S(2 * G);
        sum3 -= 0.00037 * S(p3 - p4 + o3 - o4);
        sum3 -= 0.00032 * S(3 * l3 - 7 * l4 + 2 * p3 + 2 * p4);
        sum3 += 0.00030 * S(4 * (l3 - l4));
        sum3 += 0.00029 * S(l3 + p4 - 2 * PER - 2 * G);
        sum3 -= 0.00028 * S(o3 + psi - 2 * PER - 2 * G);
        sum3 += 0.00026 * S(l3 - PER - G);
        sum3 += 0.00024 * S(l2 - 3 * l3 + 2 * l4);
        sum3 += 0.00021 * S(2 * (l3 - PER - G));
        sum3 -= 0.00021 * S(l3 - p2);
        sum3 += 0.00017 * S(2 * (l3 - p3));

        let sum4 = 0;
        sum4 += 0.84287 * S(l4 - p4);
        sum4 += 0.03431 * S(p4 - p3);
        sum4 -= 0.03305 * S(2 * (psi - PER));
        sum4 -= 0.03211 * S(G);
        sum4 -= 0.01862 * S(l4 - p3);
        sum4 += 0.01186 * S(psi - o4);
        sum4 += 0.00623 * S(l4 + p4 - 2 * G - 2 * PER);
        sum4 += 0.00387 * S(2 * (l4 - p4));
        sum4 -= 0.00284 * S(5 * Gs - 2 * G + 52.225);
        sum4 -= 0.00234 * S(2 * (psi - p4));
        sum4 -= 0.00223 * S(l3 - l4);                       // Akiyama-corrected
        sum4 -= 0.00208 * S(l4 - PER);
        sum4 += 0.00178 * S(psi + o4 - 2 * p4);
        sum4 += 0.00134 * S(p4 - PER);
        sum4 += 0.00125 * S(2 * (l4 - G - PER));
        sum4 -= 0.00117 * S(2 * G);
        sum4 -= 0.00112 * S(2 * (l3 - l4));
        sum4 += 0.00107 * S(3 * l3 - 7 * l4 + 4 * p4);
        sum4 += 0.00102 * S(l4 - G - PER);
        sum4 += 0.00096 * S(2 * l4 - psi - o4);
        sum4 += 0.00087 * S(2 * (psi - o4));
        sum4 -= 0.00085 * S(3 * l3 - 7 * l4 + p3 + 3 * p4);
        sum4 += 0.00085 * S(l3 - 2 * l4 + p4);
        sum4 -= 0.00081 * S(2 * (l4 - psi));
        sum4 += 0.00071 * S(l4 + p4 - 2 * PER - 3 * G);
        sum4 += 0.00061 * S(l1 - l4);
        sum4 -= 0.00056 * S(psi - o3);
        sum4 -= 0.00054 * S(l3 - 2 * l4 + p3);
        sum4 += 0.00051 * S(l2 - l4);
        sum4 += 0.00042 * S(2 * (psi - G - PER));
        sum4 += 0.00039 * S(2 * (p4 - o4));
        sum4 += 0.00036 * S(psi + PER - p4 - o4);
        sum4 += 0.00035 * S(2 * Gs - G + 188.37);
        sum4 -= 0.00035 * S(l4 - p4 + 2 * PER - 2 * psi);
        sum4 -= 0.00032 * S(l4 + p4 - 2 * PER - G);
        sum4 += 0.00030 * S(2 * Gs - 2 * G + 149.15);
        sum4 += 0.00029 * S(3 * l3 - 7 * l4 + 2 * p3 + 2 * p4);
        sum4 += 0.00028 * S(l4 - p4 + 2 * psi - 2 * PER);
        sum4 -= 0.00028 * S(2 * (l4 - o4));
        sum4 -= 0.00027 * S(p3 - p4 + o3 - o4);
        sum4 -= 0.00026 * S(5 * Gs - 3 * G + 188.37);
        sum4 += 0.00025 * S(o4 - o3);
        sum4 -= 0.00025 * S(l2 - 3 * l3 + 2 * l4);
        sum4 -= 0.00023 * S(3 * (l3 - l4));
        sum4 += 0.00021 * S(2 * l4 - 2 * PER - 3 * G);
        sum4 -= 0.00021 * S(2 * l3 - 3 * l4 + p4);
        sum4 += 0.00019 * S(l4 - p4 - G);
        sum4 -= 0.00019 * S(2 * l4 - p3 - p4);
        sum4 -= 0.00018 * S(l4 - p4 + G);
        sum4 -= 0.00016 * S(l4 + p3 - 2 * PER - 2 * G);

        const L1 = l1 + sum1, L2 = l2 + sum2, L3 = l3 + sum3, L4 = l4 + sum4;

        let tB1 = 0.0006393 * S(L1 - o1) + 0.0001825 * S(L1 - o2) + 0.0000329 * S(L1 - o3)
            - 0.0000311 * S(L1 - psi) + 0.0000093 * S(L1 - o4)
            + 0.0000075 * S(3 * L1 - 4 * l2 - 1.9927 * sum1 + o2)
            + 0.0000046 * S(L1 + psi - 2 * PER - 2 * G);
        let tB2 = 0.0081004 * S(L2 - o2) + 0.0004512 * S(L2 - o3) - 0.0003284 * S(L2 - psi)
            + 0.0001160 * S(L2 - o4)
            + 0.0000272 * S(l1 - 2 * l3 + 1.0146 * sum2 + o2)
            - 0.0000144 * S(L2 - o1)
            + 0.0000143 * S(L2 + psi - 2 * PER - 2 * G)
            + 0.0000035 * S(L2 - psi + G)
            - 0.0000028 * S(l1 - 2 * l3 + 1.0146 * sum2 + o3);
        let tB3 = 0.0032402 * S(L3 - o3) - 0.0016911 * S(L3 - psi) + 0.0006847 * S(L3 - o4)
            - 0.0002797 * S(L3 - o2)
            + 0.0000321 * S(L3 + psi - 2 * PER - 2 * G)
            + 0.0000051 * S(L3 - psi + G)
            - 0.0000045 * S(L3 - psi - G)
            - 0.0000045 * S(L3 + psi - 2 * PER)
            + 0.0000037 * S(L3 + psi - 2 * PER - 3 * G)
            + 0.0000030 * S(2 * l2 - 3 * L3 + 4.03 * sum3 + o2)
            - 0.0000021 * S(2 * l2 - 3 * L3 + 4.03 * sum3 + o3);
        let tB4 = -0.0076579 * S(L4 - psi) + 0.0044134 * S(L4 - o4) - 0.0005112 * S(L4 - o3)
            + 0.0000773 * S(L4 + psi - 2 * PER - 2 * G)
            + 0.0000104 * S(L4 - psi + G)
            - 0.0000102 * S(L4 - psi - G)
            + 0.0000088 * S(L4 + psi - 2 * PER - 3 * G)
            - 0.0000038 * S(L4 + psi - 2 * PER - G);

        const B1 = Math.atan(tB1), B2 = Math.atan(tB2), B3 = Math.atan(tB3), B4 = Math.atan(tB4);

        let r1 = -0.0041339 * cos((2 * (l1 - l2)) * DEG);
        r1 -= 0.0000387 * cos((l1 - p3) * DEG);
        r1 -= 0.0000214 * cos((l1 - p4) * DEG);
        r1 += 0.0000170 * cos((l1 - l2) * DEG);
        r1 -= 0.0000131 * cos((4 * (l1 - l2)) * DEG);
        r1 += 0.0000106 * cos((l1 - l3) * DEG);
        r1 -= 0.0000066 * cos((l1 + p3 - 2 * PER - 2 * G) * DEG);

        let r2 = 0.0093848 * cos((l1 - l2) * DEG);
        r2 -= 0.0003116 * cos((l2 - p3) * DEG);
        r2 -= 0.0001744 * cos((l2 - p4) * DEG);
        r2 -= 0.0001442 * cos((l2 - p2) * DEG);
        r2 += 0.0000553 * cos((l2 - l3) * DEG);
        r2 += 0.0000523 * cos((l1 - l3) * DEG);
        r2 -= 0.0000290 * cos((2 * (l1 - l2)) * DEG);
        r2 += 0.0000164 * cos((2 * (l2 - o2)) * DEG);
        r2 += 0.0000107 * cos((l1 - 2 * l3 + p3) * DEG);
        r2 -= 0.0000102 * cos((l2 - p1) * DEG);
        r2 -= 0.0000091 * cos((2 * (l1 - l3)) * DEG);

        let r3 = -0.0014388 * cos((l3 - p3) * DEG);
        r3 -= 0.0007919 * cos((l3 - p4) * DEG);
        r3 += 0.0006342 * cos((l2 - l3) * DEG);
        r3 -= 0.0001761 * cos((2 * (l3 - l4)) * DEG);
        r3 += 0.0000294 * cos((l3 - l4) * DEG);
        r3 -= 0.0000156 * cos((3 * (l3 - l4)) * DEG);
        r3 += 0.0000156 * cos((l1 - l3) * DEG);
        r3 -= 0.0000153 * cos((l1 - l2) * DEG);
        r3 += 0.0000070 * cos((2 * l2 - 3 * l3 + p3) * DEG);
        r3 -= 0.0000051 * cos((l3 + p3 - 2 * PER - 2 * G) * DEG);

        let r4 = -0.0073546 * cos((l4 - p4) * DEG);
        r4 += 0.0001621 * cos((l4 - p3) * DEG);
        r4 += 0.0000974 * cos((l3 - l4) * DEG);
        r4 -= 0.0000543 * cos((l4 + p4 - 2 * PER - 2 * G) * DEG);
        r4 -= 0.0000271 * cos((2 * (l4 - p4)) * DEG);
        r4 += 0.0000182 * cos((l4 - PER) * DEG);
        r4 += 0.0000177 * cos((2 * (l3 - l4)) * DEG);
        r4 -= 0.0000167 * cos((2 * l4 - psi - o4) * DEG);
        r4 += 0.0000167 * cos((psi - o4) * DEG);
        r4 -= 0.0000155 * cos((2 * (l4 - PER - G)) * DEG);
        r4 += 0.0000142 * cos((2 * (l4 - psi)) * DEG);
        r4 += 0.0000105 * cos((l1 - l4) * DEG);
        r4 += 0.0000092 * cos((l2 - l4) * DEG);
        r4 -= 0.0000089 * cos((l4 - PER - G) * DEG);
        r4 -= 0.0000062 * cos((l4 + p4 - 2 * PER - 3 * G) * DEG);
        r4 += 0.0000048 * cos((2 * (l4 - o4)) * DEG);

        const rad = [A[0] * (1 + r1), A[1] * (1 + r2), A[2] * (1 + r3), A[3] * (1 + r4)];
        const lon = [L1, L2, L3, L4], lat = [B1, B2, B3, B4];

        // Jupiter's equatorial frame: x west, y north, z away from the Earth.
        const xyz = [];
        for (let i = 0; i < 4; i++) {
            const ang = (lon[i] - psi) * DEG, cb = cos(lat[i]);
            xyz.push([rad[i] * cos(ang) * cb, rad[i] * sin(ang) * cb, rad[i] * sin(lat[i])]);
        }
        return { xyz, rad, psi };
    }

    /** Jupiter's geocentric ecliptic longitude and latitude, mean equinox of date, degrees. */
    function jupiterEcliptic(uJ, utcMs) {
        const ce = cos(EPS_J2000), se = sin(EPS_J2000);
        const y = uJ[1] * ce + uJ[2] * se, z = -uJ[1] * se + uJ[2] * ce;
        const T = centuries(utcMs);
        const prec = (5029.0966 * T + 1.11113 * T * T) / 3600;
        return { lon: Math.atan2(y, uJ[0]) / DEG + prec, lat: Math.asin(clamp1(z)) / DEG };
    }

    /**
     * Meeus ch. 44's rotation chain and corrections: a satellite's place in Jupiter's
     * equatorial frame into the plane of the sky, then the differential light-time and
     * perspective corrections. Without this the satellites come out in the wrong frame
     * altogether — their z stays at the tiny orbital latitude instead of running to
     * several Jupiter radii, which is what the first parity run showed.
     */
    function skyPlane(jd, distAU, node, beta0, xyz, R, psiRaw, i) {
        const tau = distAU * LIGHT_DAYS_PER_AU;
        const jc = (jd - tau - 2451545.0) / 36525;
        const OMEGA = 100.464407 + 1.0209774 * jc + 0.00040315 * jc * jc + 0.000000404 * jc * jc * jc;
        const inc = 1.303267 - 0.0054965 * jc + 0.00000466 * jc * jc - 0.000000002 * jc * jc * jc;
        const t0 = (jd - 2433282.423) / 36525;
        const psi = psiRaw + 1.3966626 * t0 + 0.0003088 * t0 * t0;   // B1950 -> of date
        const Inc = 3.120262 + 0.0006 * (jd - 2415020.5) / 36525;
        const PHI = psi - OMEGA;

        // Equatorial frame -> Jupiter's orbital plane -> node -> ecliptic -> vernal equinox
        // -> the Earth-Jupiter line. Returns (A_6, B_6, C_6): the first two span the sky
        // plane, the third runs along the Earth-Jupiter line. Getting these two the wrong
        // way round preserves |r| — so the radii still look right — while putting every
        // satellite in the wrong place, which is exactly what the first parity run showed.
        const chain = (X, Y, Z) => {
            const B1 = Y * cos(Inc * DEG) - Z * sin(Inc * DEG);
            const C1 = Y * sin(Inc * DEG) + Z * cos(Inc * DEG);
            const A2 = X * cos(PHI * DEG) - B1 * sin(PHI * DEG);
            const B2 = X * sin(PHI * DEG) + B1 * cos(PHI * DEG);
            const B3 = B2 * cos(inc * DEG) - C1 * sin(inc * DEG);
            const C3 = B2 * sin(inc * DEG) + C1 * cos(inc * DEG);
            const A4 = A2 * cos(OMEGA * DEG) - B3 * sin(OMEGA * DEG);
            const B4 = A2 * sin(OMEGA * DEG) + B3 * cos(OMEGA * DEG);
            const A5 = A4 * sin(node * DEG) - B4 * cos(node * DEG);
            const B5 = A4 * cos(node * DEG) + B4 * sin(node * DEG);
            return [A5,
                C3 * sin(beta0 * DEG) + B5 * cos(beta0 * DEG),
                C3 * cos(beta0 * DEG) - B5 * sin(beta0 * DEG)];
        };

        // D, from the fictional fifth satellite on the pole
        const [A6f, , C6f] = chain(0, 0, 1);
        const D = Math.atan2(A6f, C6f);

        const [A6, B6, C6] = chain(xyz[0], xyz[1], xyz[2]);
        let X = A6 * cos(D) - C6 * sin(D);
        const Y = A6 * sin(D) + C6 * cos(D);
        const Z = B6;

        // Differential light time, then the perspective effect (Meeus ch. 44)
        const K = [17295, 21819, 27558, 36548];
        X += (Math.abs(Z) / K[i]) * Math.sqrt(Math.max(0, 1 - (X / R) * (X / R)));
        const W = distAU / (distAU + Z / 2095);
        return [X * W, Y * W, Z];
    }

    /**
     * The four satellites on the sky, in Jupiter equatorial radii: x east, y south,
     * z towards the Earth. Meeus's equatorial frame turns into ephem's sky frame as
     * (-X, -Y, -Z) rotated in the sky plane by skyPA() — verified against ephem to
     * 0.005 deg of frame, and to the pass marks above in position.
     */
    function moons(utcMs) {
        const j = earthToJupiter(utcMs);
        const jd = utcMs / DAY_MS + 2440587.5;
        const ecl = jupiterEcliptic(j.u, utcMs);
        const g = galilean(utcMs, j.dist);
        const p = skyPA(j.u) * DEG, c = cos(p), s = sin(p);
        return MOONS.map((name, i) => {
            const sky = skyPlane(jd, j.dist, ecl.lon, ecl.lat, g.xyz[i], g.rad[i], g.psi, i);
            const mx = -sky[0], my = -sky[1], mz = -sky[2];
            const x = mx * c - my * s, y = mx * s + my * c, z = mz;
            return { name, x, y, z, rj: Math.sqrt(x * x + y * y + z * z) };
        });
    }

    // ------------------------------------------------------------------ public

    function geometry(utcMs) {
        const j = earthToJupiter(utcMs);
        const sun = earthToSun(utcMs);
        const p = pole(utcMs);

        // Sun - Jupiter, from the two geocentric vectors, all in J2000
        const jup = [j.u[0] * j.dist, j.u[1] * j.dist, j.u[2] * j.dist];
        const sunV = [sun.u[0] * sun.dist, sun.u[1] * sun.dist, sun.u[2] * sun.dist];
        const jToSun = unit([sunV[0] - jup[0], sunV[1] - jup[1], sunV[2] - jup[2]]);
        const jToEarth = [-j.u[0], -j.u[1], -j.u[2]];

        const de = Math.asin(clamp1(dot(p, jToEarth))) / DEG;
        const ds = Math.asin(clamp1(dot(p, jToSun))) / DEG;

        return {
            cm: centralMeridians(utcMs, j.u, j.dist),
            de, ds,
            polePA: polePA(j.u, utcMs),
            diameterArcsec: 196.74 / j.dist,                    // ephem's equatorial diameter at 1 AU
            distanceAU: j.dist,
            sunDistAU: j.sunDist,
            phaseAngleDeg: Math.acos(clamp1(dot(jToEarth, jToSun))) / DEG,
            lightTimeDays: j.dist * LIGHT_DAYS_PER_AU,
            sunPA: positionAngleOfDate(jToSun, j.u, utcMs),
        };
    }

    function moon(utcMs, name) {
        const i = MOONS.indexOf(name);
        if (i < 0) throw new Error('nova_jupiter: unknown moon ' + JSON.stringify(name));
        return moons(utcMs)[i];
    }

    // ------------------------------------------------------------------ phenomena

    /**
     * The sky frame at Jupiter's place, in J2000: x east, y south, z towards the Earth.
     * moons() already returns its four satellites in it, and this is what lets the Sun be
     * put in the same frame — the eclipse geometry needs both. North is the J2000 celestial
     * pole projected at Jupiter's place, so x = (-sin ra, cos ra, 0) is east and y is its
     * negative, and z is the line of sight turned round to point at the Earth.
     */
    function skyBasis(uJ) {
        const ra = Math.atan2(uJ[1], uJ[0]), dec = Math.asin(clamp1(uJ[2]));
        const sr = sin(ra), cr = cos(ra), sd = sin(dec), cd = cos(dec);
        return {
            east: [-sr, cr, 0],
            south: [sd * cr, sd * sr, -cd],
            toEarth: [-uJ[0], -uJ[1], -uJ[2]],
        };
    }

    const EVENT_STEP_MS = 10 * 60000;

    /** One instant of the phenomena scan: the four satellites and the Sun, in the sky frame. */
    function eventSample(utcMs) {
        const j = earthToJupiter(utcMs);
        const sun = earthToSun(utcMs);
        const jup = [j.u[0] * j.dist, j.u[1] * j.dist, j.u[2] * j.dist];
        const sunV = [sun.u[0] * sun.dist, sun.u[1] * sun.dist, sun.u[2] * sun.dist];
        const s = unit([sunV[0] - jup[0], sunV[1] - jup[1], sunV[2] - jup[2]]);
        const B = skyBasis(j.u);
        return {
            t: utcMs,
            m: moons(utcMs),
            sun: [dot(s, B.east), dot(s, B.south), dot(s, B.toEarth)],
        };
    }

    const sxyz = m => [m.x, m.y, m.z];

    /**
     * The function whose zero is a phenomenon's boundary, and the test for being inside one.
     * Every one of them is smooth, so a sign change between two samples brackets exactly one
     * crossing and bisection lands on it.
     *
     *   silhouette   distance from Jupiter's centre on the sky, less the disc's radius. Its
     *                zeros are where a satellite crosses the limb, seen from the Earth.
     *   depth        the satellite's distance towards the Earth, which changes sign as it
     *                passes in front of or behind the planet's centre.
     *   shadedge     distance from the Sun-Jupiter line, less one radius: the surface of the
     *                cylinder of shadow behind the planet, and at the same time the edge of
     *                the shadow the satellite itself throws on the disc - the same distance
     *                from the same line, read from the other side of the planet.
     *   sunside      the satellite's distance along the Sun-Jupiter line, which changes sign
     *                as it passes the plane through Jupiter's centre square to that line.
     *                Negative is behind the planet, where an eclipse happens; positive is in
     *                front of it, where its own shadow falls on the disc.
     */
    function boundary(s, i, which) {
        const m = s.m[i];
        switch (which) {
            case 'silhouette': return Math.hypot(m.x, m.y) - 1;
            case 'depth': return m.z;
            case 'shadedge': {
                const p = sxyz(m), a = p[0] * s.sun[0] + p[1] * s.sun[1] + p[2] * s.sun[2];
                return Math.hypot(p[0] - a * s.sun[0], p[1] - a * s.sun[1], p[2] - a * s.sun[2]) - 1;
            }
            default: return sxyz(m)[0] * s.sun[0] + sxyz(m)[1] * s.sun[1] + sxyz(m)[2] * s.sun[2];
        }
    }

    const INSIDE = {
        transit: (s, i) => s.m[i].z > 0 && Math.hypot(s.m[i].x, s.m[i].y) < 1,
        occultation: (s, i) => s.m[i].z < 0 && Math.hypot(s.m[i].x, s.m[i].y) < 1,
        eclipse: (s, i) => boundary(s, i, 'sunside') < 0 && boundary(s, i, 'shadedge') < 0,
        shadow: (s, i) => boundary(s, i, 'sunside') > 0 && boundary(s, i, 'shadedge') < 0,
    };
    const BOUNDARIES = {
        transit: ['silhouette', 'depth'],
        occultation: ['silhouette', 'depth'],
        eclipse: ['shadedge', 'sunside'],
        shadow: ['shadedge', 'sunside'],
    };
    const TYPES = ['transit', 'occultation', 'eclipse', 'shadow'];

    /**
     * The phenomena of the four satellites in a window, every one whose *start* falls in
     * [startUtcMs, endUtcMs):
     *   transit       the satellite crossing the disc, in front of the planet
     *   occultation   the satellite crossing behind the disc
     *   eclipse       the satellite inside Jupiter's shadow
     *   shadow        the satellite's own shadow crossing the disc
     * Each entry is { moon, type, startUtcMs, endUtcMs }, with endUtcMs allowed past the end
     * of the window, for a phenomenon still running when it closes. A phenomenon already
     * under way at the start is not reported: the window is the page's, and half an event
     * would be worse than none.
     *
     * A transit and its shadow transit are not the same event, though at Jupiter they are
     * always neighbours: the phase angle never exceeds 11.5 deg, so the Sun-Jupiter line is
     * never more than that from the line of sight and the shadow enters and leaves the disc
     * within a few minutes of when the satellite itself does. The shadow is ahead of the
     * satellite on the way in and behind it on the way out at eastern elongation, and the
     * other way round at western - which is the whole of what a reader of the Calendar wants
     * from the pair, and the reason the two are reported separately rather than as one row.
     *
     * The window is scanned at EVENT_STEP_MS for sign changes of the four boundary
     * functions above; each bracket is bisected to the second. A crossing that hides between
     * two samples - a grazing pass, where the function dips past zero and back inside one
     * step - is looked for at every interior local extremum whose distance from zero is
     * within a quarter of what the function changed over a step, and the extremum is
     * refined by golden section before its two crossings are bisected. That is the same
     * device nova_planets.js uses for a grazing rise, and the threshold scales with the
     * sampling rather than being a fixed number of radii. The crossings of a given boundary
     * function do not depend on which phenomenon is being assembled from them, and eclipse
     * and shadow share theirs, so each is found once per satellite and used by both.
     *
     * The model, and what it does not carry:
     *   - Jupiter's limb is the equatorial radius: the disc is taken as a circle of one
     *     radius, so its 6.5 per cent polar flattening is not in the silhouette. At the
     *     shadow's edge in the polar direction that is worth a couple of minutes; near the
     *     axis, which is where most phenomena happen, it is worth nothing.
     *   - The shadow is a cylinder of the same radius along the Sun-Jupiter line: the umbra
     *     is a cone and its radius at Callisto's distance is 0.979 of the planet's, which is
     *     inside the flattening term above.
     *   - Where a satellite's shadow lands is exact in this model: the Sun's rays through it
     *     reach the disc at arcsin(d) from the subsolar point, d being the satellite's
     *     distance from the Sun-Jupiter line in radii, so d < 1 is exactly the test for the
     *     shadow reaching the disc. The shadow is then taken as visible whenever it is on
     *     the disc. The crescent turned away from the Earth at 11.5 deg of phase is the
     *     outer 0.02 radius of the anti-solar limb - 1 - cos(phase) - so this can only cut a
     *     shadow at the very limb short, by under three minutes for Callisto's and under one
     *     for the others.
     *   - The satellites' own radii are not subtracted: a "start" is the centre of the moon
     *     crossing the limb. Io's disc is 0.013 radius, which is 0.9 minutes of its motion.
     */
    function events(startUtcMs, endUtcMs) {
        const out = [];
        if (!(endUtcMs > startUtcMs)) return out;
        const stop = endUtcMs + 6 * 3600000;      // a Callisto transit is under five hours

        const pts = [];
        for (let t = startUtcMs; ; t += EVENT_STEP_MS) {
            const at = Math.min(t, stop);
            pts.push(eventSample(at));
            if (at >= stop) break;
        }

        // Every crossing instant of one boundary function for one moon, bisected to the
        // second, remembered: eclipse and shadow are assembled from the same two.
        const crossings = new Map();
        const boundaries = (i, which) => {
            const key = i + ':' + which;
            if (crossings.has(key)) return crossings.get(key);
            const vals = pts.map(p => boundary(p, i, which));
            const times = [];
            const root = (lo, hi, loVal) => {
                let a = lo, b = hi, fa = loVal;
                while (b - a > 1000) {
                    const mid = (a + b) / 2, fm = boundary(eventSample(mid), i, which);
                    if ((fm >= 0) === (fa >= 0)) { a = mid; fa = fm; } else b = mid;
                }
                return Math.round((a + b) / 2);
            };
            for (let k = 0; k + 1 < pts.length; k++) {
                if ((vals[k] < 0) !== (vals[k + 1] < 0)) times.push(root(pts[k].t, pts[k + 1].t, vals[k]));
            }
            // A crossing hidden inside one step, with the extremum at the middle sample.
            for (let k = 1; k + 1 < pts.length; k++) {
                const isExt = (vals[k] <= vals[k - 1] && vals[k] <= vals[k + 1])
                    || (vals[k] >= vals[k - 1] && vals[k] >= vals[k + 1]);
                if (!isExt || (vals[k - 1] < 0) !== (vals[k + 1] < 0)) continue;
                const reach = 0.25 * Math.max(Math.abs(vals[k] - vals[k - 1]), Math.abs(vals[k + 1] - vals[k]));
                if (Math.abs(vals[k]) > reach) continue;
                // golden section for the extremum itself, then its two crossings
                const sgn = vals[k] <= vals[k - 1] ? 1 : -1;
                let lo = pts[k - 1].t, hi = pts[k + 1].t;
                for (let n = 0; n < 40 && hi - lo > 1000; n++) {
                    const m1 = hi - (hi - lo) * 0.618034, m2 = lo + (hi - lo) * 0.618034;
                    const v1 = sgn * boundary(eventSample(m1), i, which), v2 = sgn * boundary(eventSample(m2), i, which);
                    if (v1 > v2) hi = m2; else lo = m1;
                }
                const ext = (lo + hi) / 2;
                const ve = boundary(eventSample(ext), i, which);
                if ((ve < 0) === (vals[k - 1] < 0)) continue;      // it did not cross after all
                times.push(root(pts[k - 1].t, ext, vals[k - 1]), root(ext, pts[k + 1].t, ve));
            }
            times.sort((a, b) => a - b);
            crossings.set(key, times);
            return times;
        };

        MOONS.forEach((name, i) => {
            TYPES.forEach(type => {
                const times = [];
                BOUNDARIES[type].forEach(which => times.push(...boundaries(i, which)));
                times.sort((a, b) => a - b);
                if (!times.length) return;
                let wasInside = false;
                for (let k = 0; k < times.length; k++) {
                    const from = times[k], to = k + 1 < times.length ? times[k + 1] : stop;
                    const mid = (from + to) / 2;
                    const isInside = INSIDE[type](eventSample(mid), i);
                    if (isInside && !wasInside && from >= startUtcMs && from < endUtcMs) {
                        out.push({ moon: name, type, startUtcMs: from, endUtcMs: to });
                    }
                    wasInside = isInside;
                }
            });
        });

        return out.sort((a, b) => a.startUtcMs - b.startUtcMs);
    }

    return { MOONS, geometry, moons, moon, events, AU_KM };
});
