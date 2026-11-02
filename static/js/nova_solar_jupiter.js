/*
 * nova_solar_jupiter.js — the Solar System page's Jupiter card, and the Galilean rows
 * the Calendar takes from it.
 *
 * The card is Jupiter as a system tonight: the central meridian in System I, II and III,
 * De and Ds, the disc's size and how much of it is lit, the four Galilean moons on their
 * nearly edge-on path with the shadow each one throws on the disc, and every transit,
 * occultation, eclipse and shadow transit of the night, followed by the next nights'
 * shadow transits.
 *
 * The Calendar takes only the *double* shadow transits from it: the instants when two
 * moons' shadows are on the disc at once, in the next six months, and only while Jupiter
 * is above altitude_threshold. A single shadow transit is not calendar material — Io's
 * happens every second night — while two at once is a reason to set the alarm.
 *
 * No DOM: the page (static/js/solar_system.js) draws and formats everything.
 *
 * Built on the shared engines and the page's own night rules, looked up as globals at
 * call time, so the page only has to load them first:
 *   NovaAstro          (static/js/nova_astro.js)           time zones, sun events
 *   NovaPlanets        (static/js/nova_planets.js)         the bodies
 *   NovaJupiter        (static/js/nova_jupiter.js)         Jupiter and its moons
 *   NovaDashboardRows  (static/js/nova_dashboard_rows.js)  the instant and the night
 *   NovaSolarRows      (static/js/nova_solar_rows.js)      night window and its maximum
 *
 * opts, as every card module on this page: `inputs` (the /api/desktop_inputs response for
 * the location), `nowMs` (Date.now(), UTC ms) and `simDate` ('YYYY-MM-DD' or null).
 *
 * tonight(opts) -> {
 *   date, instantUtc, hm,          the observing night and its best instant (that night's
 *                                  highest sample, the midpoint when Jupiter is below
 *                                  the horizon all night), that instant as 'HH:MM'
 *   maxAlt, threshold, muted, mutedReason,   the night's highest altitude, and whether it
 *                                  is below the threshold ('altitude', or 'notUp' when it
 *                                  never rises)
 *   geometry,                      NovaJupiter.geometry() at the instant
 *   illuminated, mag,              from NovaPlanets.details('jupiter') at the instant
 *   moons,                         [{ name, x, y, z, rj, front, onDisc, eclipsed,
 *                                    shadow }] at the instant, in the engine's sky frame
 *                                  (x east, y south, z towards the Earth, Jupiter radii):
 *                                  front z > 0, onDisc in front of the disc's face,
 *                                  eclipsed inside the planet's shadow, shadow the point
 *                                  on the disc its own shadow falls on (null when it
 *                                  falls off it)
 *   events,                        the night's phenomena from NovaJupiter.events(), in
 *                                  time order: [{ type, moon, startUtcMs, endUtcMs, hm,
 *                                  hmDay, minutes, alt, visible, double }]. `type` is
 *                                  'transit', 'occultation', 'eclipse' or 'shadow'; `alt`
 *                                  is Jupiter's altitude at the event's midpoint and
 *                                  `visible` whether it is at or above the threshold;
 *                                  `double` true when another moon's shadow transit
 *                                  overlaps it
 *   doubles,                       how many of those events are doubles
 * }
 *
 * nextNights(opts) -> { nights: [{ date, utcMs, maxAlt }], shadows: [...] } over the next
 *   NEXT_NIGHTS nights that Jupiter reaches the threshold: the shadow transits in time
 *   order, NEXT_LIMIT of them at most, [{ date, utcMs, hm, hmDay, minutes, moons, double }]
 *   where `moons` is the one moon's name, or both when two shadows overlap (the entry is
 *   then the overlap, from the later shadow's start to the earlier one's end).
 *
 * calendarRows(opts) -> { window, rows } in the Calendar's row shape
 *   (nova_solar_calendar.js), body 'jupiter', kind 'galilean': the double shadow transits
 *   between local noon on the current observing night and local noon MONTHS_AHEAD months
 *   later, and only while Jupiter is above altitude_threshold. `moons` carries the two
 *   names, `endUtcMs` the end of the double window and `minutes` its length; `hm` is its
 *   start, `night` the observing night that contains it. `maxAlt` is the night's highest
 *   sample, the Calendar's rule, which is at or above the threshold by construction, so a
 *   row is never muted: what is below the threshold is not reported at all.
 *
 *   The search: each night's window is walked at SCAN_STEP_MS, and only where Jupiter is
 *   at or above the threshold, counting the moons whose shadow is on the disc. A step with
 *   two or more is the only thing kept, and the window's two ends are then bisected on the
 *   same test, so a row starts when the second shadow lands, or when Jupiter rises to the
 *   threshold, whichever is later, and ends at the earliest of the second shadow leaving,
 *   the planet sinking to the threshold and sunrise. The night's own end has to be in that
 *   test: Jupiter sets long after sunrise at some latitudes, and without it the bisection
 *   walked out to its bracket and a row could run ten minutes into daylight.
 *   NovaJupiter.events() cannot be used for this: it refines every boundary crossing of
 *   every moon — thousands a year — and costs 1.6 s over a six-month window, against a
 *   Calendar build of about 300 ms. What is shared here is the engine's own place for the
 *   moons (NovaJupiter.moons()) and its own Sun direction (geometry().sunPA and
 *   phaseAngleDeg, the reconstruction tools/test_jupiter.cjs checks the engine's eclipse
 *   and shadow tests against): the boundary is the engine's definition of a shadow on the
 *   disc, written out once in shadowOf(). tests/test_solar_jupiter.py pins the rows this
 *   produces to the engine's own shadow events over a bounded window, boundary for
 *   boundary, so the two cannot drift apart.
 *
 *   A double shorter than SCAN_STEP_MS at a step boundary is not seen; the refinement only
 *   extends a window it has already found, it never widens the search. Measured over the
 *   6-month window from 2026-11-02, out of 20 geocentric doubles (19 of them at least ten
 *   minutes long), London's 8 visible ones left one of 24 seconds unfound and Singapore's
 *   7 were all found; every row's ends are within about seven seconds of the same test
 *   evaluated against NovaJupiter.events()'s own intervals, whether the night cut the row
 *   short or not.
 *
 *   The rows are memoised on the night, the site, the threshold and the sampling interval,
 *   which is everything they depend on, so the Calendar's rebuild — its own key is the
 *   same set — pays for them once. Measured on this machine: 154 ms for London and 114 ms
 *   for Singapore over the 6-month window, against the Calendar's own 322 ms build.
 *
 * diagram(g, mode, moons) -> the system drawn in Jupiter's own frame, for a strip
 *   STRIP_RJ radii to each side and STRIP_HALF_HEIGHT up and down, in SVG coordinates
 *   (y down):
 *     moons    [{ name, x, y, front, onDisc, eclipsed, shadow }], x along Jupiter's
 *              equator towards its western limb and y towards its north pole, both in
 *              Jupiter radii from the disc's centre, `shadow` where that moon's shadow
 *              falls on the disc (null when it falls off it)
 *     north    unit vector towards celestial north in the figure's own frame: the pole
 *              is up in the drawing, so north is wherever polePA puts it
 *     tilt     De in degrees: how far the pole leans towards the Earth
 *   The moons ride one line across the figure because the drawing is Jupiter's, not the
 *   sky's; polePA is how that line is turned on the sky. 'diagonal' mirrors the figure
 *   left and right (a star diagonal) and 'inverted' turns it 180 degrees (a Newtonian,
 *   or a refractor without a diagonal).
 *
 * Everything throws on a missing location, a non-finite coordinate or any engine failure;
 * the page shows its error instead.
 */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.NovaSolarJupiter = factory();
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const MINUTE_MS = 60000;
    const DAY_MS = 86400000;
    const DEG = Math.PI / 180;

    const VIEWS = Object.freeze(['normal', 'diagonal', 'inverted']);
    // The strip the moons are drawn on: Callisto's apojove is 26.3 Jupiter radii, and the
    // moons stay within about 1.7 of the equator line, whose own tilt is De (3.4 deg at
    // most), so half the height is a little more than the labels need.
    const STRIP_RJ = 28;
    const STRIP_HALF_HEIGHT = 2.6;
    const SCAN_STEP_MS = 10 * MINUTE_MS;
    const MONTHS_AHEAD = 6;
    const NEXT_NIGHTS = 14;
    const NEXT_LIMIT = 8;

    function engine() {
        const A = typeof NovaAstro !== 'undefined' ? NovaAstro : null;
        const P = typeof NovaPlanets !== 'undefined' ? NovaPlanets : null;
        const J = typeof NovaJupiter !== 'undefined' ? NovaJupiter : null;
        const R = typeof NovaDashboardRows !== 'undefined' ? NovaDashboardRows : null;
        const S = typeof NovaSolarRows !== 'undefined' ? NovaSolarRows : null;
        if (!A || !P || !J || !R || !S) {
            throw new Error('nova_solar_jupiter: nova_astro.js, nova_planets.js, nova_jupiter.js, '
                + 'nova_dashboard_rows.js and nova_solar_rows.js must be loaded first');
        }
        return { A: A, P: P, J: J, W: R._wall, N: S._night };
    }

    function finite(x, what) {
        const v = typeof x === 'number' ? x : NaN;
        if (!isFinite(v)) throw new Error('nova_solar_jupiter: ' + what + ' is not a finite number');
        return v;
    }

    function parseDate(dateStr) {
        const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(dateStr));
        if (!m) throw new Error('nova_solar_jupiter: date must be YYYY-MM-DD, got ' + JSON.stringify(dateStr));
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

    /** The night's Jupiter: its highest sample in the night window (the midpoint when it
     *  is below the horizon all night), as the Moon card's. */
    function nightJupiter(date, c, sun) {
        const E = c.E, s = c.s;
        const win = E.N.nightWindow(date, s, E.A, E.W, sun);
        const times = E.N.samples(win.start, win.end, s.interval);
        const curve = E.P.altAzCurve('jupiter', times, s.lat, s.lon);
        let k = 0;
        for (let i = 1; i < curve.alt.length; i++) if (curve.alt[i] > curve.alt[k]) k = i;
        const maxAlt = finite(curve.alt[k], 'jupiter altitude');
        const utcMs = maxAlt >= 0 ? times[k] : Math.round((win.start + win.end) / 2);
        return { window: win, utcMs: utcMs, maxAlt: maxAlt };
    }

    // ------------------------------------------------------------------ the shadows

    /**
     * The Sun's direction in the sky frame NovaJupiter.moons() uses: x east, y south, z
     * towards the Earth. geometry() gives its position angle (sunPA) and its angle from
     * the Earth's direction (phaseAngleDeg), which is all it takes. This is the same
     * reconstruction tools/test_jupiter.cjs applies to the engine's own eclipse and
     * shadow tests (checkPhenomenaGeometry), and the reason it works is that Jupiter's
     * phase angle never exceeds 11.5 deg, so the Sun is always within 11.5 deg of
     * antipodal to the Earth's direction.
     */
    function sunVector(g) {
        const ph = finite(g.phaseAngleDeg, 'phase angle') * DEG;
        const pa = finite(g.sunPA, 'sunPA') * DEG;
        return [Math.sin(pa) * Math.sin(ph), -Math.cos(pa) * Math.sin(ph), Math.cos(ph)];
    }

    /**
     * Where one moon's shadow falls, from its place in the sky frame: the point where the
     * Sun-Jupiter line through the moon meets the plane through Jupiter's centre, in
     * Jupiter radii, and whether that point is on the disc.
     *
     * This is the engine's own definition, read from the other side of the planet:
     * boundary() in nova_jupiter.js measures the moon's distance from the Sun-Jupiter line
     * ('shadedge' — the same cylinder of shadow either way) and which side of the plane
     * through Jupiter's centre it is on ('sunside' — in front, where the shadow lands on
     * the disc). INSIDE.shadow is the pair of them. Written out here because the Calendar
     * needs the position, not only the yes or no, and because the engine does not export
     * it; tests/test_solar_jupiter.py pins this to the engine's events.
     */
    function shadowOf(m, S) {
        const along = m.x * S[0] + m.y * S[1] + m.z * S[2];
        const px = m.x - along * S[0], py = m.y - along * S[1], pz = m.z - along * S[2];
        const perp = Math.sqrt(px * px + py * py + pz * pz);
        return { onDisc: along > 0 && perp < 1, eclipsed: along < 0 && perp < 1, x: px, y: py };
    }

    /** One moon of the sky frame as the card and the drawing want it. */
    function moonState(m, S) {
        const sh = shadowOf(m, S);
        return {
            name: m.name, x: m.x, y: m.y, z: m.z, rj: m.rj,
            front: m.z > 0,
            onDisc: Math.sqrt(m.x * m.x + m.y * m.y) < 1,
            eclipsed: sh.eclipsed,
            shadow: sh.onDisc ? { x: sh.x, y: sh.y } : null,
        };
    }

    /** The moons whose shadow is on the disc at `utcMs`, in MOONS order. */
    function shadowsOnDisc(utcMs, E) {
        const S = sunVector(E.J.geometry(utcMs));
        const out = [];
        for (const m of E.J.moons(utcMs)) if (shadowOf(m, S).onDisc) out.push(m.name);
        return out;
    }

    // ------------------------------------------------------------------ tonight

    /** The night's phenomena, with Jupiter's altitude at each and whether it is a double. */
    function nightEvents(win, date, c) {
        const E = c.E, s = c.s, A = E.A;
        const d = parseDate(date);
        const shadows = [];
        const events = E.J.events(win.start, win.end).map(e => {
            const mid = Math.round((e.startUtcMs + e.endUtcMs) / 2);
            const wall = A.localParts(e.startUtcMs, s.tz);
            const ev = {
                type: e.type, moon: e.moon,
                startUtcMs: e.startUtcMs, endUtcMs: e.endUtcMs,
                hm: A.formatHM(e.startUtcMs, s.tz),
                hmDay: Math.round(dayNumber(wall) - dayNumber(d)),
                minutes: Math.round((e.endUtcMs - e.startUtcMs) / MINUTE_MS),
                alt: finite(E.P.altAz('jupiter', mid, s.lat, s.lon).alt, 'jupiter altitude'),
                double: false,
            };
            ev.visible = ev.alt >= s.threshold;
            if (e.type === 'shadow') shadows.push(ev);
            return ev;
        });
        // Two shadows on the disc at once: a shadow transit overlapping another moon's.
        shadows.forEach((a, i) => shadows.forEach((b, j) => {
            if (i !== j && b.moon !== a.moon && a.startUtcMs < b.endUtcMs && b.startUtcMs < a.endUtcMs) {
                a.double = true;
            }
        }));
        return events;
    }

    function tonight(opts) {
        const c = context(opts);
        const E = c.E, s = c.s;
        const night = nightJupiter(c.date, c, null);
        const g = E.J.geometry(night.utcMs);
        const S = sunVector(g);
        const det = E.P.details('jupiter', night.utcMs);
        const events = nightEvents(night.window, c.date, c);
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
            illuminated: finite(det.illuminated, 'jupiter illumination'),
            mag: finite(det.mag, 'jupiter magnitude'),
            moons: E.J.moons(night.utcMs).map(m => moonState(m, S)),
            events: events,
            doubles: events.filter(e => e.double).length,
        };
    }

    /** The look-ahead for the card: the next nights' shadow transits, doubles together. */
    function nextNights(opts) {
        const c = context(opts);
        const E = c.E, s = c.s, A = E.A;
        const start = parseDate(c.date);
        const sun = {};
        const nights = [];
        // A night whose shadows are all below the threshold is skipped, not reported
        for (let k = 1; k <= NEXT_NIGHTS && nights.length < NEXT_NIGHTS; k++) {
            const date = E.N.dateString(start.year, start.month, start.day + k);
            const night = nightJupiter(date, c, sun);
            if (night.maxAlt < s.threshold) continue;
            const found = [];
            for (const e of nightEvents(night.window, date, c)) {
                if (e.type === 'shadow' && e.visible) found.push(e);
            }
            if (!found.length) continue;
            nights.push({ date: date, utcMs: night.utcMs, maxAlt: night.maxAlt, shadows: found });
        }
        // The shadows in time order, two moons' overlapping shadow transits merged into one
        const shadows = [];
        nights.forEach(n => n.shadows.forEach(e => shadows.push({ night: n.date, e: e })));
        shadows.sort((a, b) => a.e.startUtcMs - b.e.startUtcMs);
        const out = [];
        for (let i = 0; i < shadows.length && out.length < NEXT_LIMIT; i++) {
            const a = shadows[i].e;
            const b = shadows.slice(i + 1).find(o => o.e.moon !== a.moon
                && o.e.startUtcMs < a.endUtcMs && a.startUtcMs < o.e.endUtcMs);
            const endUtcMs = b ? Math.max(a.endUtcMs, b.e.endUtcMs) : a.endUtcMs;
            const wall = A.localParts(a.startUtcMs, s.tz);
            out.push({
                date: shadows[i].night,
                utcMs: a.startUtcMs,
                hm: a.hm,
                hmDay: a.hmDay,
                minutes: Math.round((endUtcMs - a.startUtcMs) / MINUTE_MS),
                moons: b ? [a.moon, b.e.moon] : [a.moon],
                double: !!b,
            });
            if (b) i = shadows.indexOf(b);
        }
        return {
            nights: nights.map(n => ({ date: n.date, utcMs: n.utcMs, maxAlt: n.maxAlt })),
            shadows: out,
        };
    }

    // ------------------------------------------------------------------ calendar

    /**
     * How many moons' shadows are on the disc at `utcMs` while the observer is watching:
     * 0, 1 or more, and 0 whenever the planet is below the threshold or the instant is
     * outside the night window. Both bounds matter at both ends of a row: past sunrise
     * Jupiter can still be above the threshold with two shadows on it, and the night it
     * belongs to is over. This one test is the whole definition of a row's window.
     */
    function visibleShadows(utcMs, s, E, win) {
        if (utcMs < win.start || utcMs > win.end) return 0;
        if (E.P.altAz('jupiter', utcMs, s.lat, s.lon).alt < s.threshold) return 0;
        return shadowsOnDisc(utcMs, E).length;
    }

    /** The instant between `inside` and `outside` where the test becomes true, to 10 s. */
    function edge(inside, outside, s, E, win) {
        let a = inside, b = outside;
        while (Math.abs(b - a) > 10000) {
            const mid = Math.round((a + b) / 2);
            if (mid === a || mid === b) break;
            if (visibleShadows(mid, s, E, win) >= 2) a = mid; else b = mid;
        }
        return Math.round((a + b) / 2);
    }

    /** One night's double shadow transits, in order, appended to `out`. `today` is the
     *  current observing night's day number, for the row's daysFromNow. */
    function nightDoubles(date, c, sun, out, today) {
        const E = c.E, s = c.s, A = E.A, N = E.N;
        const nw = N.nightWindow(date, s, A, E.W, sun);
        const times = N.samples(nw.start, nw.end, SCAN_STEP_MS / MINUTE_MS);
        const curve = E.P.altAzCurve('jupiter', times, s.lat, s.lon);
        // The night's own maximum, the Calendar's rule: every sampling_interval minutes
        const maxAlt = N.maxAltitude('jupiter', N.samples(nw.start, nw.end, s.interval), s, E.P);
        if (maxAlt < s.threshold) return;                     // never reaches the threshold
        let run = null;
        for (let k = 0; k < times.length; k++) {
            const two = curve.alt[k] >= s.threshold && shadowsOnDisc(times[k], E).length >= 2;
            if (!two) {
                if (run) { out.push(finishRun(run, times[k], c, maxAlt, today, nw)); run = null; }
                continue;
            }
            if (!run) run = { first: times[k], prev: times[k] };
            else run.prev = times[k];
        }
        if (run) out.push(finishRun(run, nw.end + SCAN_STEP_MS, c, maxAlt, today, nw));
    }

    /** A run of flagged steps as a Calendar row: its ends bisected on the same test. */
    function finishRun(run, after, c, maxAlt, today, win) {
        const E = c.E, s = c.s, A = E.A, W = E.W;
        const startUtcMs = edge(run.first, run.first - SCAN_STEP_MS, s, E, win);
        const endUtcMs = edge(run.prev, after, s, E, win);
        const wall = A.localParts(startUtcMs, s.tz);
        const night = W.observingNight(wall);
        const nd = parseDate(night);
        const det = E.P.details('jupiter', startUtcMs);
        return {
            body: 'jupiter',
            bodies: ['jupiter'],
            kind: 'galilean',
            utcMs: startUtcMs,
            endUtcMs: endUtcMs,
            minutes: Math.round((endUtcMs - startUtcMs) / MINUTE_MS),
            moons: shadowsOnDisc(Math.round((startUtcMs + endUtcMs) / 2), E),
            night: night,
            hm: A.formatHM(startUtcMs, s.tz),
            hmDay: Math.round(dayNumber(wall) - dayNumber(nd)),
            daysFromNow: Math.round(dayNumber(nd) - today),
            mag: finite(det.mag, 'jupiter magnitude'),
            diameterArcsec: finite(det.diameterArcsec, 'jupiter diameter'),
            elongationDeg: Math.abs(finite(det.elongationDeg, 'jupiter elongation')),
            maxAlt: maxAlt,
            muted: false,                    // below the threshold it is not a row at all
            mutedReason: null,
            sepDeg: null,
            other: null,
            moment: null,
            occultation: false,
            ringTiltDeg: null,
            closestApproach: null,
        };
    }

    // The Calendar's window, and the rows, memoised on everything they depend on.
    let cache = null;

    function calendarRows(opts) {
        const c = context(opts);
        const E = c.E, s = c.s, A = E.A, W = E.W, N = E.N;
        const d = parseDate(c.date);
        const key = JSON.stringify([c.date, s.lat, s.lon, s.tz, s.threshold, s.interval, MONTHS_AHEAD]);
        if (cache && cache.key === key) return { window: cache.window, rows: cache.rows };

        const win = {
            start: W.localize(d.year, d.month, d.day, 12, 0, s.tz, A, false),
            end: W.localize(d.year, d.month + MONTHS_AHEAD, d.day, 12, 0, s.tz, A, false),
        };
        const sun = {};
        const rows = [];
        const today = dayNumber(d);
        // One night at a time, from the current observing night to the end of the window
        for (let k = 0; ; k++) {
            const date = N.dateString(d.year, d.month, d.day + k);
            const nd = parseDate(date);
            if (W.localize(nd.year, nd.month, nd.day, 12, 0, s.tz, A, false) > win.end) break;
            nightDoubles(date, c, sun, rows, today);
        }
        rows.sort((x, y) => x.utcMs - y.utcMs);
        const kept = rows.filter(r => r.utcMs <= win.end);
        cache = { key: key, window: win, rows: kept };
        return { window: win, rows: kept };
    }

    // ------------------------------------------------------------------ diagram

    function viewOf(mode) {
        if (VIEWS.indexOf(mode) < 0) throw new Error('nova_solar_jupiter: unknown view ' + JSON.stringify(mode));
        // From Jupiter's own frame (x along its equator towards its western limb, y towards
        // its north pole) to SVG (y down): the pole is up in 'normal', 'diagonal' mirrors
        // the figure left and right (a star diagonal), 'inverted' turns it 180 degrees (a
        // Newtonian). The same one line as nova_solar_moon.js: the frame is a frame of the
        // sky, so its y is up and SVG's is down.
        const sx = mode === 'normal' ? 1 : -1, sy = mode === 'inverted' ? 1 : -1;
        return (x, y) => ({ x: sx * x, y: sy * y });
    }

    const r6 = x => Math.round(x * 1e6) / 1e6;

    /**
     * The system drawn in Jupiter's own frame rather than the sky's: x along the equator
     * towards Jupiter's western limb, y towards the north pole, so the moons ride one line
     * across the figure however the pole happens to be turned on the sky (polePA), which is
     * how every printed moon diagram is drawn. Celestial north is then somewhere off that
     * frame, and is returned for the page to mark, so the figure can be matched to the sky.
     *
     * The sky frame the engine gives the moons is (east, south), so the frame's x axis is
     * the sky direction at position angle polePA - 90 — the western half of the equator —
     * and its y is the direction at polePA, the pole. Taking the pair that way round is what
     * keeps the figure off the mirror: the limb a moon drifts towards (features move from
     * the eastern limb to the western one) has to come out on the same side of the drawing
     * as it is in the eyepiece.
     */
    function diagram(g, mode, moons) {
        const view = viewOf(mode);
        const pa = finite(g.polePA, 'polePA') * DEG;
        const east = { x: -Math.cos(pa), y: -Math.sin(pa) };    // along the equator, sky frame
        const pole = { x: Math.sin(pa), y: -Math.cos(pa) };     // towards the north pole
        const place = (x, y) => view(x * east.x + y * east.y, x * pole.x + y * pole.y);
        return {
            moons: (moons || []).map(m => {
                const p = place(m.x, m.y);
                const sh = m.shadow ? place(m.shadow.x, m.shadow.y) : null;
                return {
                    name: m.name, x: r6(p.x), y: r6(p.y), front: m.front, onDisc: m.onDisc,
                    eclipsed: m.eclipsed,
                    shadow: sh ? { x: r6(sh.x), y: r6(sh.y) } : null,
                };
            }),
            // Celestial north (0, -1 in the sky frame) in the figure's own frame
            north: (p => ({ x: r6(p.x), y: r6(p.y) }))(place(0, -1)),
            tilt: finite(g.de, 'De'),
        };
    }

    return {
        VIEWS: VIEWS, STRIP_RJ: STRIP_RJ, STRIP_HALF_HEIGHT: STRIP_HALF_HEIGHT,
        SCAN_STEP_MS: SCAN_STEP_MS, MONTHS_AHEAD: MONTHS_AHEAD,
        tonight: tonight, nextNights: nextNights, calendarRows: calendarRows, diagram: diagram,
    };
});
