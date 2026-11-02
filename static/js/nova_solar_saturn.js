/*
 * nova_solar_saturn.js — the Solar System page's Saturn card, and the Iapetus rows the
 * Calendar takes from it.
 *
 * The card is Saturn as it is tonight: the ring system at its present opening angle, drawn
 * with Saturn's own north up and an arrow to celestial north, the band the rings' shadow
 * cuts across the globe, the eight satellites on their nearly edge-on path with their
 * magnitudes, and the state of the ring plane — B, which face of the rings is towards us
 * and which the Sun lights, whether the rings are opening or closing, and two years of B
 * as a sparkline. One line answers the satellite-transit question honestly: whether Titan
 * crosses the disc in the next twelve months, and which of the small moons do.
 *
 * The Calendar takes only Iapetus's western elongations: the nights its bright trailing
 * hemisphere is turned towards the Earth, every 79.33 days, in the next 24 months. Iapetus
 * is the one satellite whose brightness changes enough for an amateur to see it.
 *
 * No DOM: the page (static/js/solar_system.js) draws and formats everything.
 *
 * Built on the shared engines and the page's own night rules, looked up as globals at
 * call time, so the page only has to load them first:
 *   NovaAstro          (static/js/nova_astro.js)           time zones, sun events
 *   NovaPlanets        (static/js/nova_planets.js)         the bodies
 *   NovaSaturn         (static/js/nova_saturn.js)          Saturn, its rings and its moons
 *   NovaDashboardRows  (static/js/nova_dashboard_rows.js)  the instant and the night
 *   NovaSolarRows      (static/js/nova_solar_rows.js)      night window and its maximum
 *
 * opts, as every card module on this page: `inputs` (the /api/desktop_inputs response for
 * the location), `nowMs` (Date.now(), UTC ms) and `simDate` ('YYYY-MM-DD' or null).
 *
 * ------------------------------------------------------------------ sources
 *
 * Ring geometry: Meeus, Astronomical Algorithms, 2nd ed., ch. 45 — B (the Earth's
 * Saturnicentric latitude referred to the ring plane), B' (the Sun's), P (the position
 * angle of the northern semiminor axis of the apparent ellipse, which is the position
 * angle of the north pole of rotation), delta U (the difference of the Saturnicentric
 * longitudes of the Sun and the Earth, 0..180 deg) and the axes a and b of the outer edge
 * of the outer ring. All six come from nova_saturn.js's geometry(), which is where the
 * chapter is implemented and where the pass marks against ephem and against Horizons are
 * documented; this card only reads them. "B" and "Bprime" below are always that pair, in
 * degrees, positive towards the north face of the ring plane.
 *
 * The ring system's radii come from two quantities the engines already carry, rather than
 * from a table of kilometre values:
 *   - RING_EDGES in nova_saturn.js holds ch. 45's own fractions of a, the major axis of
 *     the outer edge: 0.8801 (inner edge of the A ring), 0.8599 (outer edge of the B ring;
 *     the two are the Cassini division), 0.6650 (inner edge of the B ring) and 0.5486
 *     (inner edge of the C, dusky ring).
 *   - The same chapter's a = 375.35 / Delta arcseconds is the outer edge's *diameter*;
 *     nova_planets.js's details('saturn').diameterArcsec is the planet's equatorial
 *     diameter at the same instant, 165.6 / Delta. Their ratio, 2.2666, is therefore the
 *     outer edge of the A ring as a multiple of Saturn's equatorial radius, and every
 *     other radius follows by multiplying by the fractions above. The two diameters cancel
 *     Delta, so this is true at any distance and needs no AU and no kilometre figure.
 * No A-ring or B-ring shadow is claimed beyond this: the C ring casts one too, and it is
 * left out (see ringShadow below).
 *
 * Satellite brightness. The magnitudes are the mean-opposition magnitudes JPL published
 * for the satellites ("Magnitude / V0 or R", legend "Mean opposition magnitude: V0, or red
 * magnitude, R"), in the last revision of "Planetary Satellite Physical Parameters" that
 * carried the column — the current page (ssd.jpl.nasa.gov/sats/phys_par/) has had it
 * removed, so the values are cited from the archived revision, snapshot 2021-09-04 of
 * ssd.jpl.nasa.gov/?sat_phys_par — Mimas 12.8, Enceladus 11.8, Tethys 10.2, Dione 10.4,
 * Rhea 9.6, Titan 8.4, Hyperion 14.4, Iapetus 11. Each is footnoted there to Morrison,
 * Johnson, Shoemaker, Soderblom, Thomas, Veverka & Smith 1984, "Satellites of Saturn:
 * Geological Perspective", in Saturn (Gehrels & Matthews, eds.), pp. 609-639, University
 * of Arizona Press. They are constants of the body, not of the night: the card shows them
 * as the satellites' brightness scale, and does not use them for anything else — in
 * particular nothing here claims that any satellite is or is not visible in a telescope.
 *
 * Iapetus's brightness varies by about two magnitudes with its position, which is the one
 * such variation worth a Calendar row. Two sources:
 *   - the BAA: "Iapetus is brightest when on the western side of Saturn and faintest when
 *     on the eastern side" (Abel & Lawrence, Observer's Challenge - Saturn vs Pluto,
 *     2019-05-26, britastro.org/2019/observers-challenge-saturn-vs-pluto);
 *   - JPL's Iapetus observing guide, "East is Least and West is Best": "a bright magnitude
 *     10.1 at western elongation and a faint 11.9 magnitude at eastern elongation",
 *     because at western elongation "we are looking at the light trailing side of tidally
 *     locked Iapetus" (Jane Houston Jones, JPL Cassini Outreach; the page is read at
 *     otastro.org/iapetus/, an amateur club's copy of the JPL-outreach piece — the text is
 *     bylined JPL and no copy on a jpl.nasa.gov host is still served).
 * The card follows both with no extra theory: Iapetus is marked as bright when it is west
 * of Saturn, faint when it is east, and the Calendar row is at its western elongation.
 * The 10.1 and 11.9 are quoted in the tooltip as that guide states them; the 11 above is
 * the mean-opposition magnitude of the same body, a different quantity, and the card does
 * not subtract one from the other.
 *
 * ------------------------------------------------------------------ what it returns
 *
 * tonight(opts) -> {
 *   date, instantUtc, hm,           the observing night and its best instant (the night's
 *                                   highest sample of Saturn, the midpoint when Saturn is
 *                                   below the horizon all night), that instant as 'HH:MM'
 *   maxAlt, threshold, muted, mutedReason,   the night's highest altitude, and whether it
 *                                   is below the threshold ('altitude', or 'notUp' when it
 *                                   never rises)
 *   geometry,                       NovaSaturn.geometry() at the instant
 *   mag, diameterArcsec, illuminated, elongationDeg,   from NovaPlanets.details('saturn')
 *   rings: {
 *     B, Bprime, deltaU, polePA, aArcsec, bArcsec,   the engine's, unchanged
 *     radiusRs,                     the outer edge of the A ring, in Saturn equatorial
 *                                   radii (2.2666, see above)
 *     edges,                        [{ key, inner, outer }] innermost first, in Saturn
 *                                   radii: 'c' the dusky ring, 'b', 'a' and 'division'
 *                                   between the last two (inner === outer there, so the
 *                                   page can draw it as a gap)
 *     face, sunFace,                'north' or 'south': the face of the ring plane towards
 *                                   the Earth, and the one towards the Sun
 *     sameFace,                     whether those are the same face
 *     trend,                        'opening' or 'closing': |B| a month from the instant
 *                                   against B now, so what the rings are doing
 *     months,                       [{ utcMs, B }] every SPARK_STEP_DAYS for SPARK_MONTHS
 *                                   months, the sparkline
 *   }
 *   moons,                          [{ name, labelled, x, y, z, r, dist, side, inFront,
 *                                     onDisc, behindDisc, mag, brightness }] from
 *                                   NovaSaturn.moons() at the instant, in the engine's sky
 *                                   frame (x east, y south, z towards the Earth, Saturn
 *                                   equatorial radii). `r` is the distance as the drawing
 *                                   projects it, `dist` the moon's distance in space.
 *                                   `side` is 'east' or 'west' (the drawing's own frame is
 *                                   Saturn's, so the tooltip has to say which), `onDisc` is
 *                                   in front of the globe and inside its limb, `behindDisc`
 *                                   hidden behind it, `labelled` whether the drawing names
 *                                   it (Titan, Rhea, Tethys, Dione, Iapetus), `mag` the V0
 *                                   above, and `brightness` 'bright', 'faint' or null —
 *                                   Iapetus's western elongation or eastern, the rest null
 *   transits: {
 *     crossing,                     [{ name, labelled, closestRs, utcMs }] the moons whose
 *                                   orbit comes inside the disc in the next twelve months,
 *                                   innermost first: `closestRs` the closest their centre
 *                                   comes to the centre of the disc, `utcMs` when
 *                                   `titan`,                       Titan's own entry from that list, or null
 *     titanNext,                    { closestRs, utcMs } the next twelve months after now
 *                                   in which Titan does, or null within the engine's own
 *                                   reference (NEXT_SEASON_YEARS, see below)
 *   }
 * }
 *
 * The two scans behind `transits` are the whole of the card's look-ahead, and they are
 * deliberately not an events engine. A satellite is taken to cross the disc when the
 * closest its centre comes to the centre of the disc, over an orbit, is less than one
 * Saturn radius. That quantity is exact enough to be checked by hand — it is the semi-minor
 * axis of the apparent ellipse of the orbit, a sin(beta), reached where the moon is nearest
 * the line of sight, in front of Saturn or behind it — and it is found by walking the window
 * two hours at a time, locating each extremum of the angle from the three samples that
 * bracket it (see seedOf), and closing on the distance with a golden section. Measured
 * against a season scanned a minute at a time over six seasons, that walk gives every one of
 * the eight the same closest approach to within 0.0032 radii, and it is never shallower than
 * the scan by more than 0.0005: where the two differ the card is usually the deeper of them,
 * because the scan samples the crossing and the walk locates it. The instants of the
 * crossings themselves are not computed, and the card claims none: a transit, an occultation
 * and the partial ones between are all "crossing the disc" here. Titan's next season is the
 * same walk over the engine's own reference, which ephem's BDL data ends at the end of 2039
 * (nova_saturn.js says so in its header): within that limit the card can say when Titan next
 * crosses, and past it it says nothing. The two scans together are the card's cost: 0.19 to
 * 0.31 s for the season, and 0.40 s when Titan's next season has to be searched too, once
 * per night.
 *
 * calendarRows(opts) -> { window, rows } in the Calendar's row shape
 *   (nova_solar_calendar.js), body 'saturn', kind 'iapetusWest': Iapetus's western
 *   elongations from local noon on the current observing night to local noon MONTHS_AHEAD
 *   months later, from NovaSaturn.elongations(). `utcMs` is the instant of the elongation
 *   and `hm` its local time; the night is the observing night that contains it, and the
 *   row carries Saturn's own magnitude, diameter and elongation from the Sun at that
 *   instant, with `ringTiltDeg` B, as the Calendar's other Saturn rows do. Muted, reason
 *   'altitude' or 'notUp', when Saturn's own highest sample that night is below
 *   altitude_threshold — the Calendar's rule. The rows are memoised on the night, the site,
 *   the threshold and the sampling interval, as the Jupiter card's are.
 *
 * diagram(g, mode, moons, radiusRs) -> the system drawn in Saturn's own frame, Saturn
 *   equatorial radii from the centre, x towards the sky direction at polePA - 90 and y
 *   towards the north pole, so the pole is up whatever PA it has, then turned by `mode`:
 *   'diagonal' mirrors the figure left and right (a star diagonal) and 'inverted' turns it
 *   180 degrees (a Newtonian). y is *up* in this frame, as the sky's is; the page's own
 *   viewOf() and this one are the same line.
 *     rings     [{ key, d }] one path per edge, in the order the page should fill them
 *               (see rings.edges: 'division' is the gap between the A and the B ring and
 *               is drawn as an empty band, so the two edges of the ring it separates only
 *               have to be filled; the page draws each region as an annulus in one path)
 *     shadow    the rings' shadow on the globe, one closed path, or null when there is
 *               none (the rings are exactly edge-on to the Sun, or open so far that the
 *               shadow leaves the disc). This is the band the ring plane cuts off from the
 *               sunlight: a globe point is in shadow when the Sun's ray through it crosses
 *               the ring plane between the B ring's inner edge and the A ring's outer edge.
 *               The C ring's own shadow is left out — it is the duskily drawn one and its
 *               opacity is a fraction of the B ring's, so a band drawn to its inner edge
 *               would overstate the shadow. What falls inside the disc is the page's
 *               business: it clips the path to the globe.
 *     moons     [{ name, x, y, r, labelled, inside, off}], one entry per moon in DRAWN and
 *               in that order. `inside` is true while the moon's own x is within STRIP_RS
 *               of the centre, and x and y are then the place the engine puts it, which the
 *               page draws a dot at. `off` is 'east', 'west' or null and is set only for a
 *               moon outside that -- whose place the strip cannot reach -- and x and y are
 *               then pulled back to the edge of the strip *along the moon's own line from
 *               the centre*, so the direction they give is still its own and the page can
 *               follow that line to wherever it leaves the figure and mark it there
 *     north     { x, y } the sky direction of celestial north in the figure's own frame
 *     tilt      B, in degrees
 *   The frame is the same rotation nova_solar_jupiter.js uses for its moons: `east` is the
 *   sky direction at polePA - 90 (Saturn's equator, towards the western limb, since the
 *   pole leans that way) and `pole` the direction at polePA. The ring ellipse comes out of
 *   it with no further work — its major axis is along `east`, its minor along `pole` — which
 *   is what P means: the position angle of the semiminor axis. So the rings are always
 *   drawn with the same radii and the same |sin B| flattening, and the moons ride the same
 *   line the rings do. The engine's own list has eight moons; the diagram draws DRAWN, the
 *   seven the card's figure is for, and the ones beyond its edge are the ones the card
 *   cannot show at a legible scale (Titan at its greatest elongation is 20.8 radii out,
 *   Iapetus 60.7, and a figure wide enough for Iapetus leaves the globe two pixels across),
 *   which are marked at the edge instead.
 *
 * Everything throws on a missing location, a non-finite coordinate or any engine failure;
 * the page shows its error instead.
 */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.NovaSolarSaturn = factory();
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const MINUTE_MS = 60000;
    const DAY_MS = 86400000;
    const DEG = Math.PI / 180;

    const VIEWS = Object.freeze(['normal', 'diagonal', 'inverted']);
    // The strip the system is drawn on, each side of Saturn's centre, in Saturn equatorial
    // radii. The globe is 1 and the A ring's outer edge 2.2666, so the rings are 16 per
    // cent of the half-width and the globe 7: both read as what they are, and the moons
    // that fit (Mimas to Rhea every night, Titan on the nights it is not near its greatest
    // elongation) are drawn where they are. See the header for why it is not wider.
    const STRIP_RS = 14;
    // The moons whose names the drawing sets. Mimas and Enceladus are drawn as small
    // unnamed dots with their names and magnitudes in their tooltips: at this scale their
    // names would sit on the ring system.
    const LABELLED = Object.freeze(['Titan', 'Rhea', 'Tethys', 'Dione', 'Iapetus']);
    // The moons the diagram shows, in the engine's own order. Hyperion, the eighth, is left
    // out: at 24 radii out it is past the strip's edge every night of the cycle, so the
    // figure could only ever mark it with a chevron at its own edge, which says nothing
    // that the others do not and crowds the edge with a name.
    const DRAWN = Object.freeze(['Mimas', 'Enceladus', 'Tethys', 'Dione', 'Rhea', 'Titan',
                                 'Iapetus']);
    const MONTHS_AHEAD = 24;
    const SEASON_DAYS = 365;
    const NEXT_SEASON_YEARS = 14;
    const SPARK_MONTHS = 24;
    const SPARK_STEP_DAYS = 15;
    // A month, for the opening-or-closing question: long enough that the yearly wobble of
    // B is not what the answer is about.
    const TREND_DAYS = 30;
    // The engine's reference ends with ephem's BDL data at the end of 2039
    // (nova_saturn.js, "the reference's own limit"): nothing past it is asked for.
    const REFERENCE_END_MS = Date.UTC(2040, 0, 1);
    // How close together the two edges of a shadow outline may be drawn before the
    // outline is cut and closed with a straight line, in Saturn radii of the drawing
    // frame: 0.0002 is a two-hundredth of a pixel at the scale the page uses, so the shape
    // is the shadow's own and not a trace of the arithmetic.
    const SHADOW_MIN_GAP = 0.0002;
    // The satellite magnitudes V0, JPL (see the header): visual magnitude at mean
    // opposition, a constant of the body.
    const MOON_MAG = Object.freeze({
        Mimas: 12.8, Enceladus: 11.8, Tethys: 10.2, Dione: 10.4,
        Rhea: 9.6, Titan: 8.4, Hyperion: 14.4, Iapetus: 11.0,
    });
    // What the card's credit line names: the two sources the header cites for the
    // magnitudes, in the shape the page's other cards take theirs in. `short` is the same
    // two sources for the line itself, which carries `credit` in its own tooltip: the
    // satellites' magnitudes are JPL's, Iapetus's own two are JPL/Cassini's.
    const SOURCE = Object.freeze({
        credit: 'JPL Solar System Dynamics, Planetary Satellite Physical Parameters '
            + '(mean-opposition magnitudes, archived 2021-09-04); Iapetus’s 10.1 and 11.9 '
            + 'from JPL/Cassini Outreach, “East is Least and West is Best”',
        short: 'JPL; Iapetus: JPL/Cassini',
        date: '2021-09-04',
    });

    function engine() {
        const A = typeof NovaAstro !== 'undefined' ? NovaAstro : null;
        const P = typeof NovaPlanets !== 'undefined' ? NovaPlanets : null;
        const S = typeof NovaSaturn !== 'undefined' ? NovaSaturn : null;
        const R = typeof NovaDashboardRows !== 'undefined' ? NovaDashboardRows : null;
        const N = typeof NovaSolarRows !== 'undefined' ? NovaSolarRows : null;
        if (!A || !P || !S || !R || !N) {
            throw new Error('nova_solar_saturn: nova_astro.js, nova_planets.js, nova_saturn.js, '
                + 'nova_dashboard_rows.js and nova_solar_rows.js must be loaded first');
        }
        return { A: A, P: P, S: S, W: R._wall, N: N._night };
    }

    function finite(x, what) {
        const v = typeof x === 'number' ? x : NaN;
        if (!isFinite(v)) throw new Error('nova_solar_saturn: ' + what + ' is not a finite number');
        return v;
    }

    function parseDate(dateStr) {
        const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(dateStr));
        if (!m) throw new Error('nova_solar_saturn: date must be YYYY-MM-DD, got ' + JSON.stringify(dateStr));
        return { year: +m[1], month: +m[2], day: +m[3] };
    }

    const dayNumber = d => Date.UTC(d.year, d.month - 1, d.day) / DAY_MS;

    /** The current instant, the observing night and the site, as the Bodies tab has them. */
    function context(opts) {
        const o = opts || {};
        const E = engine();
        const s = E.N.site(o.inputs);
        const now = E.W.effectiveInstant(finite(o.nowMs, 'nowMs'), o.simDate, s.tz, E.A);
        return { E: E, s: s, now: now, date: E.W.observingNight(now.wall) };
    }

    /** The night's Saturn: its highest sample in the night window (the midpoint when it is
     *  below the horizon all night), as the other cards have it. */
    function nightSaturn(date, c, sun) {
        const E = c.E, s = c.s;
        const win = E.N.nightWindow(date, s, E.A, E.W, sun);
        const times = E.N.samples(win.start, win.end, s.interval);
        const curve = E.P.altAzCurve('saturn', times, s.lat, s.lon);
        let k = 0;
        for (let i = 1; i < curve.alt.length; i++) if (curve.alt[i] > curve.alt[k]) k = i;
        const maxAlt = finite(curve.alt[k], 'saturn altitude');
        const utcMs = maxAlt >= 0 ? times[k] : Math.round((win.start + win.end) / 2);
        return { window: win, utcMs: utcMs, maxAlt: maxAlt };
    }

    // ------------------------------------------------------------------ the ring plane

    const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
    const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
    const norm3 = v => Math.sqrt(dot(v, v));
    const unit3 = v => { const n = norm3(v); return [v[0] / n, v[1] / n, v[2] / n]; };

    /**
     * The Sun's direction in the sky frame NovaSaturn.moons() uses: x east, y south, z
     * towards the Earth. geometry() gives its position angle (sunPA) and its angle from the
     * Earth's direction (phaseAngleDeg), which is all it takes — the same reconstruction
     * nova_solar_jupiter.js applies to Jupiter's, and it works for the same reason: Saturn's
     * phase angle never exceeds 6.5 deg, so the Sun is within 6.5 deg of the direction to
     * the Earth, and the sky frame's z is that direction. The dot product with the ring
     * normal below comes out as sin B' = sin B cos ph + cos B sin ph cos(sunPA - polePA),
     * which is the spherical law of cosines and not an approximation.
     */
    function sunVector(g) {
        const ph = finite(g.phaseAngleDeg, 'phase angle') * DEG;
        const pa = finite(g.sunPA, 'sunPA') * DEG;
        return [Math.sin(pa) * Math.sin(ph), -Math.cos(pa) * Math.sin(ph), Math.cos(ph)];
    }

    /**
     * The ring plane's north pole as a unit vector in that same sky frame. Its sky
     * projection lies at position angle polePA — that is what P is — and its z is sin B:
     * with x east and y south, the direction at position angle P is (sin P, -cos P, 0).
     * So the ring's apparent ellipse has its major axis along (cos P, -sin P) and its minor
     * along this, with |sin B| of it in the sky plane.
     */
    function ringNormal(g) {
        const pa = finite(g.polePA, 'polePA') * DEG;
        const b = finite(g.B, 'B') * DEG;
        return [Math.cos(b) * Math.sin(pa), -Math.cos(b) * Math.cos(pa), Math.sin(b)];
    }

    /** The ring system's radii in Saturn equatorial radii, innermost first. */
    function ringEdges(g, det) {
        const edges = engine().S.RING_EDGES;
        const diameter = finite(det.diameterArcsec, 'saturn diameter');
        // 375.35 / Delta over 165.6 / Delta: the two diameters cancel it
        const radius = finite(g.aArcsec, 'ring major axis') / diameter;
        return {
            radiusRs: radius,
            edges: [
                { key: 'c', inner: radius * edges.duskyInner, outer: radius * edges.innerInner },
                { key: 'b', inner: radius * edges.innerInner, outer: radius * edges.innerOuter },
                { key: 'division', inner: radius * edges.innerOuter, outer: radius * edges.outerInner },
                { key: 'a', inner: radius * edges.outerInner, outer: radius * edges.outerOuter },
            ],
        };
    }

    // ------------------------------------------------------------------ the shadow

    /**
     * The rings' shadow on the globe, as a closed outline in the drawing frame.
     *
     * A globe point G is in the rings' shadow when the Sun's ray through it, G + t S (S the
     * unit vector towards the Sun), crosses the ring plane between the B ring's inner edge
     * and the A ring's outer edge. Writing G = alpha S + q with q square to S, the crossing
     * is at |Q|^2 = |q|^2 (1 + gamma^2), gamma = (q/|q| . P) / (S . P), P the ring plane's
     * pole — so the shadowed part of the globe is, in the sun-square plane, the set of
     * directions whose radius from the Sun-Saturn line falls between r_in / sqrt(1+gamma^2)
     * and r_out / sqrt(1+gamma^2). S . P is sin B', so with psi measured in that plane from
     * the sky projection of the pole, gamma = cot(B') cos(psi), and the whole outline
     * follows: the band is an arc centred on the pole's side of the disc, and it vanishes
     * when |B'| is too small for the ring's inner edge to reach the globe at all — which is
     * what happens as the rings close to the Sun edge-on.
     *
     * The two edges are not the same kind of thing, and it is worth keeping them apart:
     * the inner edge is where the Sun's ray grazes the B ring's inner edge, while the outer
     * edge is the ring plane's own reach *or the terminator*, whichever comes first — a
     * point of the globe is at radius 1 from the Sun-Saturn line exactly when the Sun is on
     * its horizon, so there is nothing beyond that to darken. Where the terminator is the
     * bound, the shadow runs right up to it, and since the Earth is never more than 6.5
     * degrees from the Sun as seen from Saturn, the terminator is within half a per cent of
     * the limb we see, so the band's ends reach the edge of the disc.
     *
     * That also makes the outline cross itself, by a few thousandths of a Saturn radius,
     * where the terminator edge and the inner edge meet at each end of the arc: the two run
     * into each other at a shallow angle, and the projected shadow's boundary is a hair
     * from folding over. It is drawn anyway — the filled result is right to within a
     * fortieth of a pixel at the page's scale (measured over 2024-2039), and cutting it off
     * would blunt the band's ends visibly instead. The page's clip to the disc covers the
     * rest.
     *
     * It is drawn as one closed path in the figure's frame, `view` applied. Returns null
     * when the Sun is in the ring plane (no shadow at all) and when the band's two ends
     * have met everywhere (the rings too open to cast one on the globe from where we are).
     */
    function ringShadow(g, radiusRs, view) {
        const edges = engine().S.RING_EDGES;
        const P = ringNormal(g), S = sunVector(g);
        const D = dot(S, P);                          // sin B'
        if (Math.abs(D) < 1e-9) return null;          // the Sun in the ring plane
        const rb = finite(radiusRs, 'ring radius');
        const rIn = rb * edges.innerInner;            // the B ring's inner edge
        const rOut = rb * edges.outerOuter;           // the A ring's outer edge
        if (!(rOut > rIn) || rOut <= 1) return null;  // nothing of the ring is outside the globe
        const cot = Math.cos(finite(g.Bprime, 'Bprime') * DEG) / D;
        // The band exists while |gamma| >= sqrt(rIn^2 - 1), which is |cos psi| >= this
        const cMin = Math.tan(Math.abs(finite(g.Bprime, 'Bprime') * DEG)) * Math.sqrt(rIn * rIn - 1);
        if (!(cMin < 1)) return null;                 // never reaches: too near the Sun's ring plane

        // The sun-square plane's own basis: e1 towards the pole's part of it, e2 square to both
        const perp = [P[0] - D * S[0], P[1] - D * S[1], P[2] - D * S[2]];
        if (!(norm3(perp) > 1e-9)) return null;
        const e1 = unit3(perp), e2 = cross(S, e1);

        const at = psi => {
            const gamma = cot * Math.cos(psi);
            const s = Math.sqrt(1 + gamma * gamma);
            return { lo: rIn / s, hi: Math.min(1, rOut / s) };
        };
        const centre = cot < 0 ? 0 : Math.PI;         // gamma < 0: cos psi against the sign of cot
        const half = Math.acos(cMin);

        const point = (psi, rho) => {
            const alpha = Math.sqrt(Math.max(0, 1 - rho * rho));
            const q = [Math.cos(psi) * e1[0] + Math.sin(psi) * e2[0],
                       Math.cos(psi) * e1[1] + Math.sin(psi) * e2[1],
                       Math.cos(psi) * e1[2] + Math.sin(psi) * e2[2]];
            return [alpha * S[0] + rho * q[0], alpha * S[1] + rho * q[1], alpha * S[2] + rho * q[2]];
        };
        // The shadow lives in the sky frame; the figure's is the same with y up
        const pa = finite(g.polePA, 'polePA') * DEG;
        const east = [-Math.cos(pa), -Math.sin(pa), 0], pole = [Math.sin(pa), -Math.cos(pa), 0];
        const project = G => view(dot(G, east), dot(G, pole));

        // The two edges meet at both ends of the arc, where the band comes to a point. The
        // outline is cut a hair inside those points and closed with a straight line: how
        // far the band's own width shrinks there says nothing about how far apart the two
        // edges *look*, because the globe's surface is nearly edge-on to us, so the cut is
        // on the gap between the two edges as drawn. It is a two-hundredth of a pixel at
        // the page's scale: the eyes see a band that comes to a point at the edge of the
        // disc, and nothing else.
        const gap = psi => {
            const e = at(psi);
            const a = project(point(psi, e.lo)), b = project(point(psi, e.hi));
            return Math.hypot(a.x - b.x, a.y - b.y);
        };
        const cut = (from, to) => {                   // first psi from `from` towards `to` that is a band
            let lo = 0, hi = 1;                       // bisected along the arc, so either direction works
            for (let i = 0; i < 40 && hi - lo > 1e-9; i++) {
                const mid = (lo + hi) / 2;
                if (gap(from + (to - from) * mid) >= SHADOW_MIN_GAP) hi = mid; else lo = mid;
            }
            return from + (to - from) * hi;
        };
        const a0 = cut(centre - half, centre), a1 = cut(centre + half, centre);
        if (!(a1 - a0 > 1e-6)) return null;

        const r5 = x => Math.round(x * 1e5) / 1e5;
        // The steps are clustered towards the two ends of the arc, where the band comes to
        // a point: the two edges' separation grows fastest from there, and a uniform step
        // long enough to be worth drawing would let one edge's step cross the other's.
        const step = i => (1 - Math.cos(Math.PI * i / SHADOW_STEPS)) / 2;
        const out = [];
        for (let i = 0; i <= SHADOW_STEPS; i++) {
            const psi = a0 + (a1 - a0) * step(i);
            const p = project(point(psi, at(psi).lo));
            out.push((i ? 'L ' : 'M ') + r5(p.x) + ' ' + r5(p.y));
        }
        for (let i = SHADOW_STEPS; i >= 0; i--) {
            const psi = a0 + (a1 - a0) * step(i);
            const p = project(point(psi, at(psi).hi));
            out.push('L ' + r5(p.x) + ' ' + r5(p.y));
        }
        return out.join(' ') + ' Z';
    }

    const SHADOW_STEPS = 96;

    // ------------------------------------------------------------------ the moons

    /** One moon of the sky frame as the card and the drawing want it. `r` is the moon's
     *  distance from the centre as the drawing projects it and `dist` its distance in space,
     *  which is the one a tooltip can call a distance from Saturn: a moon in front of or
     *  behind the globe projects onto the middle of the disc. */
    function moonEntry(m) {
        const r = Math.hypot(m.x, m.y);
        const inside = r < 1;
        return {
            name: m.name,
            labelled: LABELLED.indexOf(m.name) >= 0,
            x: m.x, y: m.y, z: m.z, r: r,
            dist: Math.hypot(m.x, m.y, m.z),
            side: m.x < 0 ? 'west' : 'east',
            inFront: m.z > 0,
            onDisc: m.z > 0 && inside,
            behindDisc: !(m.z > 0) && inside,
            mag: MOON_MAG[m.name],
            brightness: m.name !== 'Iapetus' ? null : m.x < 0 ? 'bright' : 'faint',
        };
    }

    // ------------------------------------------------------------------ the season

    /** The best (lowest) sample of `f` in [lo, hi], by golden section: nova_saturn.js's own
     *  closing rule, used on the distance from the centre of the disc. */
    function bestNear(f, lo, hi, seedT, seedR) {
        let best = { t: seedT, r: seedR };
        const keep = (t, r) => { if (r < best.r) best = { t: t, r: r }; };
        let a = lo, b = hi;
        let m1 = b - (b - a) * 0.618034, m2 = a + (b - a) * 0.618034;
        let f1 = f(m1), f2 = f(m2);
        keep(m1, f1); keep(m2, f2);
        for (let n = 0; n < 60 && b - a > 10000; n++) {
            if (f1 < f2) { b = m2; m2 = m1; f2 = f1; m1 = b - (b - a) * 0.618034; f1 = f(m1); }
            else { a = m1; m1 = m2; f1 = f2; m2 = a + (b - a) * 0.618034; f2 = f(m2); }
            keep(m1, f1); keep(m2, f2);
        }
        return best;
    }

    // The walk's step, and the half-width of the window the golden section closes in. Mimas
    // orbits in 0.94 d, so the moments it is nearest the centre of the disc — one per half
    // orbit, 11 h apart — are the finest feature of the season any of the eight has, and the
    // step has to put a sample either side of one of them. The fourteen-year Titan search
    // below walks a day at a time instead: Titan's are 8 d apart, and a season of them costs
    // a day's samples rather than a couple of hours'.
    const CROSS_STEP_MS = 2 * 60 * MINUTE_MS;
    const TITAN_STEP_MS = DAY_MS;
    const SEED_SLACK = 0.05;

    /** The angle between a moon and the line of sight, 0 dead in front of or behind Saturn
     *  and 90 at the edge of the disc. r = rho sin(beta), so the least r over an orbit is at
     *  the least beta, and beta is what a walk can bracket with three samples. Either side of
     *  Saturn counts: a moon occulted by the globe is as close to the line of sight as one
     *  transiting it, and Mimas reaches 0.002 radii that way. */
    function sightAngle(m) { return Math.atan2(m.r, Math.abs(m.z)); }

    /**
     * Where an orbit comes nearest the centre, from the three samples that bracket it, as a
     * time and the distance there. Two estimates, and the better of the two is kept:
     *
     *   - the parabola through the sight angle at the three samples. Exact where the moon
     *     misses the disc, because the angle is very nearly a sinusoid in the moon's own
     *     phase; where it crosses, the angle is a V and the parabola's vertex lands up to a
     *     radius of arc away from the crossing (Mimas: 1.05 radii worst over a season, 0.37
     *     on average).
     *   - the foot of the perpendicular from the centre to the chord joining the outer two
     *     samples. The projected path of a crossing moon is a straight line through the
     *     centre, so this is the crossing itself and is good to a thousandth of a radius
     *     (worst 0.41 radii over a season, 0.005 on average, and it degrades gracefully:
     *     where the path curves away the foot is still a time worth asking the engine about).
     *
     * Taking the lesser of the two is what makes the pruning below sound. The seed is r at a
     * time the samples bracket, so it is always at or above the true value — but only the
     * chord's is close to it where the moon crosses, and a seed a radius too high is what
     * dropped Tethys's crossing in June 2024.
     */
    function seedOf(h, name, E, step) {
        const bend = h[0].b - 2 * h[1].b + h[2].b;
        if (!bend) return null;
        const at = Math.round(h[1].t + step * (h[0].b - h[2].b) / (2 * bend));
        let seed = { t: at, r: E.S.moon(at, name).r };
        const A = h[0], B = h[2], dx = B.x - A.x, dy = B.y - A.y, L2 = dx * dx + dy * dy;
        if (L2 > 0) {
            const s = -(A.x * dx + A.y * dy) / L2;      // the foot, as a fraction of the chord
            const foot = Math.round(A.t + (B.t - A.t) * Math.max(0, Math.min(1, s)));
            const rf = E.S.moon(foot, name).r;
            if (rf < seed.r) seed = { t: foot, r: rf };
        }
        return seed;
    }

    /**
     * Each named moon's closest approach to the centre of the disc over `days` from `start`.
     * Over one orbit that approach is the semi-minor axis of the apparent ellipse of the
     * orbit, a sin(beta) with beta the Earth's latitude above the orbit's plane, and the
     * moon reaches it twice per orbit — where it is nearest the line of sight, which is an
     * extremum of the angle between the two. Those are found first, because they are the
     * sharp feature and the value at them is the smooth one: the walk brackets each extremum
     * with three samples, and `seedOf` above locates it from them. The season's answer is the
     * least of those, and a golden section closes on it. Sampling the distance itself and
     * closing on the best sample, which is what this
     * used to do, does not work for the six inner moons: a day of Mimas is two and a half
     * orbits, so the samples land at whatever phase they land at, and the distance between
     * two of its minima is a whole radius — the phase, not beta, picks the winner. Nor is
     * the extremum of z the same moment: that is where the moon is furthest from the sky
     * plane, and for an eccentric orbit it can be hours away from the nearest to the line of
     * sight (eight hours for Hyperion, whose e is 0.1, and two and a half radii out).
     */
    function closestOver(start, days, names, E, stepMs) {
        const step = stepMs || CROSS_STEP_MS;
        const end = start + days * DAY_MS;
        const out = {}, hist = {}, cands = {};
        names.forEach(n => { out[n] = null; });
        for (let t = start; t <= end; t += step) {
            for (const m of E.S.moons(t)) {
                if (names.indexOf(m.name) < 0) continue;
                const h = hist[m.name] || (hist[m.name] = []);
                h.push({ t: t, b: sightAngle(m), x: m.x, y: m.y });
                if (h.length > 3) h.shift();
                if (h.length < 3) continue;
                const d1 = h[1].b - h[0].b, d2 = h[2].b - h[1].b;
                if ((d1 > 0) === (d2 > 0)) continue;      // the angle not turning at the middle sample
                const seed = seedOf(h, m.name, E, step);
                if (seed) (cands[m.name] || (cands[m.name] = [])).push(seed);
            }
        }
        // The candidates within SEED_SLACK of a moon's own best seed are closed on properly,
        // and the rest of its season is dropped: the seeds are r at times the samples bracket,
        // so no candidate's true value is above its own seed, and only a seed that is wildly
        // too high can hide one. The chord seed above is what keeps that from happening where
        // a moon crosses — closing on the hundred-odd candidates a crossing season leaves for
        // Mimas costs about 20 ms, and closing on all fifteen hundred of them costs 400.
        // (Measured against every orbit of all eight moons in four seasons, the true best
        // candidate is among those kept every time; on the parabola's seed alone it was missed
        // by up to 0.0026 radii.)
        names.forEach(n => {
            const list = cands[n];
            if (!list || !list.length) return;
            list.sort((a, b) => a.r - b.r);
            const floor = list[0].r;
            let best = list[0];
            list.forEach(c => {
                if (c.r > floor + SEED_SLACK) return;
                // The window is the step either side of the seed, which is where the three
                // samples that produced it bracket the extremum: the parabola can put its
                // vertex well outside the middle sample when the angle turns sharply, and
                // Titan's turns sharply indeed at a day's walk (its own window is two days,
                // and its approaches are 8 d apart, so it still holds only the one).
                const near = bestNear(t => E.S.moon(t, n).r, c.t - step, c.t + step, c.t, c.r);
                if (near.r < best.r) best = near;
            });
            out[n] = best;
        });
        return out;
    }

    /** The moons that cross the disc in the season (SEASON_DAYS from `nowMs`), innermost
     *  first, with the instant their closest approach happens. */
    function seasonCrossings(nowMs, E) {
        const names = E.S.MOONS.slice();
        const found = closestOver(nowMs, SEASON_DAYS, names, E);
        const out = [];
        names.forEach(n => {
            const c = found[n];
            if (c && c.r < 1) out.push({ name: n, labelled: LABELLED.indexOf(n) >= 0, closestRs: c.r, utcMs: Math.round(c.t) });
        });
        return out.sort((a, b) => a.closestRs - b.closestRs);
    }

    /** The first season after `nowMs` in which Titan crosses the disc, or null within the
     *  engine's reference. One year at a time, because a season's own answer is not the
     *  question: this asks when the next one is, and the walk is cheap enough to repeat. */
    function nextTitanSeason(nowMs, E) {
        for (let y = 0; y < NEXT_SEASON_YEARS; y++) {
            const start = nowMs + y * SEASON_DAYS * DAY_MS;
            if (start > REFERENCE_END_MS) return null;
            const c = closestOver(start, SEASON_DAYS, ['Titan'], E, TITAN_STEP_MS).Titan;
            if (c && c.r < 1) return { closestRs: c.r, utcMs: Math.round(c.t) };
        }
        return null;
    }

    // ------------------------------------------------------------------ tonight

    // The card's night is the whole of what tonight() depends on, and the two scans behind
    // it are the card's cost (~50 ms), so it is kept until the night or the site changes:
    // the page re-renders on every view change and every control on the tab, and none of
    // those move Saturn.
    let nightCache = null;

    function tonight(opts) {
        const c = context(opts);
        const E = c.E, s = c.s;
        const key = JSON.stringify([c.date, s.lat, s.lon, s.tz, s.threshold, s.interval]);
        if (nightCache && nightCache.key === key) return nightCache.value;
        const value = tonightAt(c);
        nightCache = { key: key, value: value };
        return value;
    }

    function tonightAt(c) {
        const E = c.E, s = c.s;
        const night = nightSaturn(c.date, c, null);
        const g = E.S.geometry(night.utcMs);
        const det = E.P.details('saturn', night.utcMs);
        const rings = ringEdges(g, det);
        const months = [];
        for (let k = 0; k <= SPARK_MONTHS * 30 / SPARK_STEP_DAYS; k++) {
            const t = night.utcMs + k * SPARK_STEP_DAYS * DAY_MS;
            months.push({ utcMs: t, B: finite(E.S.geometry(t).B, 'B') });
        }
        const trendB = finite(E.S.geometry(night.utcMs + TREND_DAYS * DAY_MS).B, 'B');
        const crossing = seasonCrossings(night.utcMs, E);
        const titan = crossing.filter(x => x.name === 'Titan');
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
            mag: finite(det.mag, 'saturn magnitude'),
            diameterArcsec: finite(det.diameterArcsec, 'saturn diameter'),
            illuminated: finite(det.illuminated, 'saturn illumination'),
            elongationDeg: finite(det.elongationDeg, 'saturn elongation'),
            rings: {
                B: g.B, Bprime: g.Bprime, deltaU: g.deltaU, polePA: g.polePA,
                aArcsec: g.aArcsec, bArcsec: g.bArcsec,
                radiusRs: rings.radiusRs, edges: rings.edges,
                face: g.B < 0 ? 'south' : 'north',
                sunFace: g.Bprime < 0 ? 'south' : 'north',
                sameFace: (g.B < 0) === (g.Bprime < 0),
                trend: Math.abs(trendB) > Math.abs(g.B) ? 'opening' : 'closing',
                months: months,
            },
            moons: E.S.moons(night.utcMs).map(moonEntry),
            transits: {
                crossing: crossing.map(x => ({ name: x.name, labelled: x.labelled,
                                               closestRs: x.closestRs, utcMs: x.utcMs })),
                titan: titan.length ? { closestRs: titan[0].closestRs, utcMs: titan[0].utcMs } : null,
                titanNext: titan.length ? null : nextTitanSeason(night.utcMs, E),
            },
        };
    }

    // ------------------------------------------------------------------ calendar

    let cache = null;

    function calendarRows(opts) {
        const c = context(opts);
        const E = c.E, s = c.s, A = E.A, W = E.W, N = E.N;
        const d = parseDate(c.date);
        const key = JSON.stringify([c.date, s.lat, s.lon, s.tz, s.threshold, s.interval, MONTHS_AHEAD]);
        if (cache && cache.key === key) return { window: cache.window, rows: cache.rows };

        const noonOf = (y, m, day) => W.localize(y, m, day, 12, 0, s.tz, A, false);
        const win = { start: noonOf(d.year, d.month, d.day, 12, 0), end: noonOf(d.year, d.month + MONTHS_AHEAD, d.day) };
        const today = dayNumber(d);
        const sun = {};
        const rows = E.S.elongations(win.start, win.end).map(e => {
            const wall = A.localParts(e.utcMs, s.tz);
            const night = W.observingNight(wall);
            const nd = parseDate(night);
            const nw = N.nightWindow(night, s, A, W, sun);
            const maxAlt = N.maxAltitude('saturn', N.samples(nw.start, nw.end, s.interval), s, E.P);
            const det = E.P.details('saturn', e.utcMs);
            const muted = maxAlt < s.threshold;
            return {
                body: 'saturn',
                bodies: ['saturn'],
                kind: 'iapetusWest',
                utcMs: e.utcMs,
                night: night,
                hm: A.formatHM(e.utcMs, s.tz),
                hmDay: Math.round(dayNumber(wall) - dayNumber(nd)),
                daysFromNow: Math.round(dayNumber(nd) - today),
                mag: finite(det.mag, 'saturn magnitude'),
                diameterArcsec: finite(det.diameterArcsec, 'saturn diameter'),
                elongationDeg: Math.abs(finite(det.elongationDeg, 'saturn elongation')),
                maxAlt: maxAlt,
                muted: muted,
                mutedReason: muted ? (maxAlt < 0 ? 'notUp' : 'altitude') : null,
                sepDeg: null,
                other: null,
                moment: null,
                occultation: false,
                ringTiltDeg: finite(det.ringTiltDeg, 'saturn ring tilt'),
                closestApproach: null,
            };
        });
        rows.sort((x, y) => x.utcMs - y.utcMs);
        cache = { key: key, window: win, rows: rows };
        return { window: win, rows: rows };
    }

    // ------------------------------------------------------------------ diagram

    function viewOf(mode) {
        if (VIEWS.indexOf(mode) < 0) throw new Error('nova_solar_saturn: unknown view ' + JSON.stringify(mode));
        // Saturn's own frame (x towards the sky direction at P - 90, y towards the pole) to
        // SVG: the pole is up in 'normal', 'diagonal' mirrors the figure left and right (a
        // star diagonal), 'inverted' turns it 180 degrees (a Newtonian). The same one line
        // as the other cards'.
        const sx = mode === 'normal' ? 1 : -1, sy = mode === 'inverted' ? 1 : -1;
        return (x, y) => ({ x: sx * x, y: sy * y });
    }

    const r6 = x => Math.round(x * 1e6) / 1e6;

    /** One annulus, as one path: the outer ellipse one way, the inner the other. */
    function annulus(inner, outer, ry, fmt) {
        const o = v => fmt(v), rx = v => fmt(v);
        return 'M ' + o(-outer) + ' 0 A ' + rx(outer) + ' ' + fmt(outer * ry) + ' 0 1 0 ' + o(outer) + ' 0'
            + ' A ' + rx(outer) + ' ' + fmt(outer * ry) + ' 0 1 0 ' + o(-outer) + ' 0 Z'
            + ' M ' + o(-inner) + ' 0 A ' + rx(inner) + ' ' + fmt(inner * ry) + ' 0 1 1 ' + o(inner) + ' 0'
            + ' A ' + rx(inner) + ' ' + fmt(inner * ry) + ' 0 1 1 ' + o(-inner) + ' 0 Z';
    }

    /**
     * The system in Saturn's own frame. The ring system is the ring plane's own ellipse —
     * the sky direction at polePA - 90 is the major axis and the direction at polePA the
     * minor, of length |sin B| — so it needs no rotation of its own: the figure is already
     * turned so the pole's projection is straight up. The moons are placed in the same
     * rotation, which is the same one nova_solar_jupiter.js gives Jupiter's.
     */
    function diagram(g, mode, moons, radiusRs) {
        const view = viewOf(mode);
        const pa = finite(g.polePA, 'polePA') * DEG;
        const east = { x: -Math.cos(pa), y: -Math.sin(pa) };
        const pole = { x: Math.sin(pa), y: -Math.cos(pa) };
        const place = (x, y) => view(x * east.x + y * east.y, x * pole.x + y * pole.y);

        const sinB = Math.abs(Math.sin(finite(g.B, 'B') * DEG));
        const rb = finite(radiusRs, 'ring radius');
        const edges = engine().S.RING_EDGES;
        // An ellipse over the origin is the same figure mirrored, so the view cannot change
        // it and the paths are built in the frame's own coordinates
        const rings = [
            { key: 'c', d: annulus(rb * edges.duskyInner, rb * edges.innerInner, sinB, r6) },
            { key: 'b', d: annulus(rb * edges.innerInner, rb * edges.innerOuter, sinB, r6) },
            { key: 'a', d: annulus(rb * edges.outerInner, rb * edges.outerOuter, sinB, r6) },
        ];

        const drawn = (moons || []).filter(m => DRAWN.indexOf(m.name) >= 0).map(m => {
            const p = place(m.x, m.y);
            const over = Math.max(Math.abs(p.x), 0);
            const off = over > STRIP_RS ? (p.x < 0 ? 'west' : 'east') : null;
            // Pulled back to the edge of the strip along the moon's own line from the centre:
            // the figure cannot reach the place itself, but the direction is the moon's own
            const x = off ? (p.x < 0 ? -STRIP_RS : STRIP_RS) : p.x;
            const y = off ? p.y * (STRIP_RS / over) : p.y;
            return { name: m.name, labelled: m.labelled, r: m.r, inside: !off,
                     off: off, x: r6(x), y: r6(y) };
        });

        return {
            rings: rings,
            shadow: ringShadow(g, rb, view),
            moons: drawn,
            // Celestial north (0, -1 in the sky frame) in the figure's own frame
            north: (p => ({ x: r6(p.x), y: r6(p.y) }))(place(0, -1)),
            tilt: finite(g.B, 'B'),
        };
    }

    return {
        VIEWS: VIEWS, STRIP_RS: STRIP_RS, LABELLED: LABELLED, DRAWN: DRAWN,
        MONTHS_AHEAD: MONTHS_AHEAD, MOON_MAG: MOON_MAG, SOURCE: SOURCE,
        tonight: tonight, calendarRows: calendarRows, diagram: diagram,
        // for the tests: the pieces, without a location or a night in the way
        _ringNormal: ringNormal, _ringShadow: ringShadow, _sunVector: sunVector,
        _closestOver: closestOver, _moonEntry: moonEntry, _viewOf: viewOf,
    };
});
