/*
 * nova_solar_rigfit.js — the Solar System page's planetary calculator, worked out in the browser.
 *
 * For the selected body and a chosen telescope, camera and amplification: the disc in
 * pixels, the focal ratio against the ideal planetary one, how much detail the aperture
 * can show, and for Jupiter, Saturn and Mars the rotation limit, for the Moon the mosaic.
 * No DOM: the page (static/js/solar_system.js) renders the lines and formats the numbers.
 *
 * A telescope and a camera are the page's embedded records (core.py _solar_equipment):
 * the raw component fields { uid, name, aperture_mm, focal_length_mm } and { uid, name,
 * pixel_size_um, sensor_width_mm, sensor_height_mm }, so each value is worked out from
 * the fields it needs and only that value is missing when one of them is. The
 * amplification (a Barlow or extender, 1 without one) is one of AMPLIFICATIONS.
 *
 * metrics(telescope, camera, amp) follows _compute_rig_metrics_from_components
 * (nova/helpers.py) formula by formula, the amplification in the reducer factor's place,
 * so the numbers match what Nova shows elsewhere:
 *   efl        = focal_length_mm x amp
 *   fRatio     = efl / aperture_mm
 *   scale      = 206.265 x pixel_size_um / efl                      arcsec per pixel
 *   fovW, fovH = 2 atan(sensor / 2 / efl), in arcminutes
 * except that a missing aperture leaves the image scale and field of view alone (the
 * server's helper drops them all); only the focal ratio needs the aperture.
 *
 * fit(telescope, camera, amp, body, diameterArcsec)
 *   -> { disc, fRatio, ideal, status, detail, rotation, mosaic }.
 * Each value is { ok: true, ... } or { ok: false, missing: <code> }; detail is null for
 * the Moon, rotation and mosaic for the bodies they do not apply to. Missing codes, the
 * first that applies: 'focalLength', 'aperture', 'pixelSize', 'sensorSize',
 * 'sensorImplausible' (a sensor size that cannot be millimetres: see plausibleSensor);
 * then 'notComputable' when the fields are there but an input is not usable (an amplification
 * not in AMPLIFICATIONS, a body's diameter that is not positive) or a result is not a
 * finite number (absurd values).
 *   disc      { px, ringsPx (Saturn only, else null) }: diameter / scale
 *   fRatio    { value }: the focal ratio with the amplification
 *   ideal     { value }: IDEAL_PER_UM x pixel size (the common rule f ≈ 5 x pixel in µm)
 *   status    { value }: ratioStatus(fRatio, ideal)
 *   detail    { value ('fine' | 'main' | 'discOnly'), elements }: detail(diameter, aperture)
 *   rotation  { seconds }: rotationSeconds(body, aperture, diameter)
 *   mosaic    mosaicPanels(diameter, fovW, fovH, OVERLAP)
 *
 * summary(fit, maxAltitude, best, current) -> { level, step }: the calculator's one-line
 * verdict for beginners from the fit, the body's highest altitude tonight, and the best
 * and the current amplification (see summary below).
 *
 * preview(telescope, camera, amp, body, diameterArcsec, illuminatedFraction, ringTiltDeg)
 * -> the scale preview's geometry in sensor pixels (see preview below), or
 * { ok: false, missing } like the values above.
 */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.NovaSolarRigFit = factory();
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const PLATE_SCALE = 206.265;       // arcsec per radian / 1000: µm over mm
    const IDEAL_PER_UM = 5;
    const DAWES_ARCSEC_MM = 116;
    const AMPLIFICATIONS = Object.freeze([1, 1.5, 2, 2.5, 3, 4, 5]);
    const ON_TARGET_BAND = 0.15;       // within 15 % of the ideal: on target
    // Resolution elements across the disc: at least DETAIL_FINE shows fine detail, at least
    // DETAIL_MAIN the main features, fewer the disc only.
    const DETAIL_FINE = 40;
    const DETAIL_MAIN = 15;
    const OVERLAP = 0.20;
    // Below this highest altitude tonight the atmosphere blurs a planet: the setup is limited
    const SUMMARY_MIN_ALTITUDE = 30;
    // Sidereal rotation periods in hours (System III for Jupiter and Saturn)
    const ROTATION_HOURS = Object.freeze({ jupiter: 9.925, saturn: 10.56, mars: 24.62 });
    // Saturn's outer ring diameter over its equatorial diameter: the A ring's outer edge,
    // 136 775 km, over the equatorial radius, 60 268 km (NASA Saturn Fact Sheet) = 2.27.
    const SATURN_RING_FACTOR = 136775 / 60268;
    // The rings' inner edge as drawn: the B ring's inner edge, 92 000 km, over the
    // equatorial radius (NASA Saturn Fact Sheet) = 1.53; the faint C ring inside is left out.
    const SATURN_RING_INNER_FACTOR = 92000 / 60268;
    // The scale preview: the width it is laid out for (CSS px; it shrinks on a narrow
    // window), the smallest disc drawn at that width before it is drawn not to scale, the
    // margin an ROI keeps around the body, and the common ROI sizes, smallest first.
    const PREVIEW_WIDTH = 320;
    const PREVIEW_MIN_DISC = 4;
    const ROI_MARGIN = 0.25;
    const ROI_SIZES = Object.freeze([[320, 240], [640, 480], [800, 600], [1024, 768], [1280, 960], [1920, 1080]]);
    // Bodies drawn with their phase; a fraction this close to 1 is drawn full
    const PHASE_BODIES = Object.freeze(['mercury', 'venus', 'moon', 'mars']);
    // A sensor side over this many mm, or over this many pixels (side / pixel size), is not
    // a real sensor: most likely pixel counts entered as millimetres
    const PLAUSIBLE_SENSOR_MM = 60;
    const PLAUSIBLE_SENSOR_PX = 20000;
    const FULL_PHASE = 0.995;

    const DEG = Math.PI / 180;

    /** A positive finite number, or null. */
    function num(x) {
        return typeof x === 'number' && isFinite(x) && x > 0 ? x : null;
    }

    const missing = code => ({ ok: false, missing: code });

    /** The amplification when it is one of AMPLIFICATIONS, else null. */
    function ampOf(amp) {
        return AMPLIFICATIONS.indexOf(amp) >= 0 ? amp : null;
    }

    /** `value` when each of its `keys` is a finite number (or null), else 'notComputable'. */
    function finiteOr(value, keys) {
        const bad = keys.some(k => value[k] !== null && !(typeof value[k] === 'number' && isFinite(value[k])));
        return bad ? missing('notComputable') : value;
    }

    function metrics(telescope, camera, amp) {
        const t = telescope || {}, c = camera || {};
        const fl = num(t.focal_length_mm);
        const ap = num(t.aperture_mm);
        const px = num(c.pixel_size_um);
        const sw = num(c.sensor_width_mm);
        const sh = num(c.sensor_height_mm);
        const a = ampOf(amp);
        const efl = fl !== null && a !== null ? fl * a : null;
        const fov = s => (s !== null && efl !== null) ? 2 * Math.atan((s / 2.0) / efl) / DEG * 60.0 : null;
        return {
            efl: efl,
            fRatio: (efl !== null && ap !== null) ? efl / ap : null,
            scale: (efl !== null && px !== null) ? PLATE_SCALE * px / efl : null,
            fovW: fov(sw), fovH: fov(sh),
            aperture: ap, pixel: px,
        };
    }

    /** False for a sensor size that cannot be millimetres: a side over PLAUSIBLE_SENSOR_MM,
     *  or (with a pixel size) over PLAUSIBLE_SENSOR_PX pixels. */
    function plausibleSensor(camera) {
        const c = camera || {};
        const sw = num(c.sensor_width_mm), sh = num(c.sensor_height_mm), px = num(c.pixel_size_um);
        if (sw === null || sh === null) return true;    // missing, not implausible
        if (sw > PLAUSIBLE_SENSOR_MM || sh > PLAUSIBLE_SENSOR_MM) return false;
        return px === null || (sw * 1000 / px <= PLAUSIBLE_SENSOR_PX && sh * 1000 / px <= PLAUSIBLE_SENSOR_PX);
    }

    /** The first missing field among the telescope's and the camera's, in that order. */
    function why(telescope, camera, needs) {
        const t = telescope || {}, c = camera || {};
        if (needs.indexOf('focalLength') >= 0 && num(t.focal_length_mm) === null) return 'focalLength';
        if (needs.indexOf('aperture') >= 0 && num(t.aperture_mm) === null) return 'aperture';
        if (needs.indexOf('pixelSize') >= 0 && num(c.pixel_size_um) === null) return 'pixelSize';
        if (needs.indexOf('sensorSize') >= 0
            && (num(c.sensor_width_mm) === null || num(c.sensor_height_mm) === null)) return 'sensorSize';
        if (needs.indexOf('sensorSize') >= 0 && !plausibleSensor(c)) return 'sensorImplausible';
        return null;
    }

    /** 'onTarget' within ON_TARGET_BAND of the ideal (edges included), else 'below' or 'above' it. */
    function ratioStatus(fRatio, ideal) {
        if (Math.abs(fRatio / ideal - 1) <= ON_TARGET_BAND + 1e-9) return 'onTarget';
        return fRatio < ideal ? 'below' : 'above';
    }

    /**
     * How much real detail the aperture can show: the diameter over the telescope's
     * resolution (Dawes, 116 / aperture arcsec), in resolution elements.
     */
    function detail(diameterArcsec, apertureMm) {
        const elements = diameterArcsec / (DAWES_ARCSEC_MM / apertureMm);
        const value = elements >= DETAIL_FINE ? 'fine'
            : elements >= DETAIL_MAIN ? 'main' : 'discOnly';
        return { value: value, elements: elements };
    }

    /**
     * The amplification whose focal ratio is closest to the ideal one, on a log scale
     * (the smaller on a tie); 1 when the telescope or camera lacks the data for it.
     */
    function bestAmplification(telescope, camera) {
        const m = metrics(telescope, camera, 1);
        if (m.fRatio === null || m.pixel === null) return 1;
        const k = IDEAL_PER_UM * m.pixel / m.fRatio;
        if (!(isFinite(k) && k > 0)) return 1;
        let best = AMPLIFICATIONS[0];
        for (const a of AMPLIFICATIONS) if (Math.abs(Math.log(a / k)) < Math.abs(Math.log(best / k))) best = a;
        return best;
    }

    /** The record with the best `key` by `better`; records without a usable value come last. */
    function pick(list, key, better) {
        let best = null;
        for (const r of list || []) {
            if (!r) continue;
            if (best === null) { best = r; continue; }
            const v = num(r[key]), b = num(best[key]);
            if (v !== null && (b === null || better(v, b))) best = r;
        }
        return best;
    }

    /** The telescope with the largest aperture (the first on a tie), or null. */
    function defaultTelescope(list) {
        return pick(list, 'aperture_mm', (v, b) => v > b);
    }

    /** The camera with the smallest pixel size (the first on a tie), or null. */
    function defaultCamera(list) {
        return pick(list, 'pixel_size_um', (v, b) => v < b);
    }

    /**
     * The longest capture before rotation blurs detail at the disc centre: the time the
     * centre takes to move one resolution element (Dawes, 116 / aperture arcsec) at
     * pi x diameter per rotation period. Seconds.
     */
    function rotationSeconds(body, apertureMm, diameterArcsec) {
        const hours = ROTATION_HOURS[body];
        if (!hours) throw new Error('nova_solar_rigfit: no rotation period for ' + JSON.stringify(body));
        return (DAWES_ARCSEC_MM / apertureMm) * hours * 3600 / (Math.PI * diameterArcsec);
    }

    /**
     * Frames for the full disc, a grid with `overlap` of a frame between neighbours: per
     * axis 1 when the disc fits, else 1 + ceil((d - F) / ((1 - overlap) F)). The disc is
     * round, so turning the camera does not change the count.
     */
    function mosaicPanels(diameterArcsec, fovWArcmin, fovHArcmin, overlap) {
        const d = diameterArcsec / 60;
        const o = overlap === undefined ? OVERLAP : overlap;
        const axis = f => (d <= f ? 1 : 1 + Math.ceil((d - f) / ((1 - o) * f) - 1e-9));
        const cols = axis(fovWArcmin), rows = axis(fovHArcmin);
        return { cols: cols, rows: rows, panels: cols * rows };
    }

    /**
     * The calculator's verdict. level: 'limited' when a needed value is missing (the
     * focal-ratio status, the altitude, and for a planet the detail), when the body stays
     * below SUMMARY_MIN_ALTITUDE or a planet shows only its disc; else 'excellent' when the
     * focal ratio is on target with fine detail (the Moon: on target alone); else 'good'.
     * step, the most useful next step, the first that applies:
     *   { kind: 'low', altitude }        below SUMMARY_MIN_ALTITUDE
     *   { kind: 'amp', amp }             off target: the best amplification, when not the current one
     *   { kind: 'bestAlready' }          off target, and the current one is already the best
     *   { kind: 'smallDisc' }            a planet that shows only its disc
     *   { kind: 'onTarget', detail }     on target ('fine' | 'main', null for the Moon)
     * or null (the status is missing).
     */
    function summary(f, maxAltitude, best, current) {
        const alt = typeof maxAltitude === 'number' && isFinite(maxAltitude) ? maxAltitude : null;
        const status = f && f.status && f.status.ok ? f.status.value : null;
        const moon = !f || f.detail === null;
        const det = !moon && f.detail.ok ? f.detail.value : null;

        let level = 'good';
        if (alt === null || status === null || (!moon && det === null)
            || alt < SUMMARY_MIN_ALTITUDE || det === 'discOnly') level = 'limited';
        else if (status === 'onTarget' && (moon || det === 'fine')) level = 'excellent';

        let step = null;
        if (alt !== null && alt < SUMMARY_MIN_ALTITUDE) step = { kind: 'low', altitude: alt };
        else if (status === 'below' || status === 'above') {
            step = best !== current ? { kind: 'amp', amp: best } : { kind: 'bestAlready' };
        } else if (det === 'discOnly') step = { kind: 'smallDisc' };
        else if (status === 'onTarget') step = { kind: 'onTarget', detail: moon ? null : det };
        return { level: level, step: step };
    }

    /**
     * The lit part of a disc (centre cx, cy, radius r) with `fraction` illuminated, lit
     * side to the right: the right half-circle, then back along the terminator, an
     * ellipse of half-width r |2 fraction - 1| that bulges left when gibbous and right
     * when a crescent. An SVG path; null when the disc is (all but) full.
     */
    function phasePath(cx, cy, r, fraction) {
        if (!(fraction < FULL_PHASE)) return null;
        const k = Math.max(0, fraction);
        const a = r * Math.abs(2 * k - 1);
        const sweep = k >= 0.5 ? 1 : 0;
        return 'M ' + cx + ' ' + (cy - r) + ' A ' + r + ' ' + r + ' 0 0 1 ' + cx + ' ' + (cy + r)
            + ' A ' + a + ' ' + r + ' 0 0 ' + sweep + ' ' + cx + ' ' + (cy - r) + ' Z';
    }

    /**
     * Half of Saturn's ring annulus (outer half-axes rx, ry; inner ones scaled by
     * SATURN_RING_INNER_FACTOR / SATURN_RING_FACTOR) around cx, cy: `upper` the half above
     * the centre. An SVG path.
     */
    function ringHalf(cx, cy, rx, ry, upper) {
        const f = SATURN_RING_INNER_FACTOR / SATURN_RING_FACTOR;
        const ix = rx * f, iy = ry * f;
        const s = upper ? 1 : 0;
        return 'M ' + (cx - rx) + ' ' + cy + ' A ' + rx + ' ' + ry + ' 0 0 ' + s + ' ' + (cx + rx) + ' ' + cy
            + ' L ' + (cx + ix) + ' ' + cy + ' A ' + ix + ' ' + iy + ' 0 0 ' + (1 - s) + ' ' + (cx - ix) + ' ' + cy + ' Z';
    }

    /** The smallest of ROI_SIZES that holds a w x h body with ROI_MARGIN around it and is
     *  smaller than the sensor (W x H); null when none is. */
    function roiFor(w, h, W, H) {
        for (const [rw, rh] of ROI_SIZES) {
            if (rw >= w * (1 + ROI_MARGIN) && rh >= h * (1 + ROI_MARGIN)) {
                return rw <= W && rh <= H && (rw < W || rh < H) ? { w: rw, h: rh } : null;
            }
        }
        return null;
    }

    /**
     * The scale preview, in sensor pixels: the sensor (its pixel dimensions, sensor size /
     * pixel size) with the body at its centre at exact scale. Missing codes as in fit.
     *   sensor   { w, h }: the rectangle at 0, 0
     *   disc     { cx, cy, r (drawn), px (exact diameter), notToScale }: drawn PREVIEW_MIN_DISC
     *            px across at PREVIEW_WIDTH when smaller (Saturn's rings scaled with it)
     *   phase    { fraction, path } for PHASE_BODIES not (all but) full, else null
     *   rings    { rx, ry, back, front } for Saturn: ry = rx |sin B|; the half nearer to us
     *            (below the centre with the north face in view, B > 0) is drawn in front
     *   mosaic   { cols, rows, frames: [{ x, y, w, h }] } for the Moon when it takes more
     *            than one frame: mosaicPanels' grid, neighbours overlapping by OVERLAP
     *   roi      { w, h, x, y }: roiFor the body (Saturn: with its rings), planets only
     *   viewBox  { x, y, w, h }: everything, with a margin
     */
    function preview(telescope, camera, amp, body, diameterArcsec, illuminatedFraction, ringTiltDeg) {
        const f = fit(telescope, camera, amp, body, diameterArcsec);
        const c = camera || {};
        if (!f.disc.ok) return f.disc;
        const code = why(telescope, camera, ['sensorSize']);
        if (code) return missing(code);
        const W = Math.round(c.sensor_width_mm * 1000 / c.pixel_size_um);
        const H = Math.round(c.sensor_height_mm * 1000 / c.pixel_size_um);
        if (!(W >= 1 && H >= 1 && isFinite(W) && isFinite(H))) return missing('notComputable');

        const cx = W / 2, cy = H / 2;
        const px = f.disc.px;
        const tilt = body === 'saturn' && typeof ringTiltDeg === 'number' && isFinite(ringTiltDeg) ? ringTiltDeg : null;
        const ringsPx = body === 'saturn' ? f.disc.ringsPx : null;

        // The extent before any not-to-scale enlargement decides the drawing's width
        const extent = Math.max(W, ringsPx || px);
        const minDrawn = PREVIEW_MIN_DISC * (extent * 1.1) / PREVIEW_WIDTH;
        const notToScale = px < minDrawn;
        const k = notToScale ? minDrawn / px : 1;
        const r = px * k / 2;

        let rings = null;
        if (ringsPx !== null) {
            const rx = ringsPx * k / 2;
            const ry = tilt === null ? 0 : rx * Math.abs(Math.sin(tilt * DEG));
            const northInView = tilt === null || tilt >= 0;
            rings = { rx: rx, ry: ry, tiltDeg: tilt,
                back: ringHalf(cx, cy, rx, ry, northInView), front: ringHalf(cx, cy, rx, ry, !northInView) };
        }

        let phase = null;
        if (PHASE_BODIES.indexOf(body) >= 0 && typeof illuminatedFraction === 'number' && isFinite(illuminatedFraction)) {
            const path = phasePath(cx, cy, r, illuminatedFraction);
            if (path) phase = { fraction: illuminatedFraction, path: path };
        }

        let mosaic = null;
        if (body === 'moon' && f.mosaic && f.mosaic.ok && f.mosaic.panels > 1) {
            const stepX = (1 - OVERLAP) * W, stepY = (1 - OVERLAP) * H;
            const gw = W + (f.mosaic.cols - 1) * stepX, gh = H + (f.mosaic.rows - 1) * stepY;
            const frames = [];
            for (let j = 0; j < f.mosaic.rows; j++) {
                for (let i = 0; i < f.mosaic.cols; i++) {
                    frames.push({ x: cx - gw / 2 + i * stepX, y: cy - gh / 2 + j * stepY, w: W, h: H });
                }
            }
            mosaic = { cols: f.mosaic.cols, rows: f.mosaic.rows, frames: frames };
        }

        let roi = null;
        if (body !== 'moon') {
            const bw = ringsPx !== null ? ringsPx : px;
            const bh = rings !== null ? Math.max(px, 2 * rings.ry / k) : px;
            const size = roiFor(bw, bh, W, H);
            if (size) roi = { w: size.w, h: size.h, x: cx - size.w / 2, y: cy - size.h / 2 };
        }

        // Everything drawn, with a margin of 5 % of its width
        let x0 = 0, y0 = 0, x1 = W, y1 = H;
        const grow = (x, y, w, h) => { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x + w); y1 = Math.max(y1, y + h); };
        const hw = rings ? rings.rx : r;
        grow(cx - hw, cy - r, 2 * hw, 2 * r);
        if (mosaic) mosaic.frames.forEach(fr => grow(fr.x, fr.y, fr.w, fr.h));
        const pad = (x1 - x0) * 0.05;
        const viewBox = { x: x0 - pad, y: y0 - pad, w: x1 - x0 + 2 * pad, h: y1 - y0 + 2 * pad };

        return {
            ok: true, sensor: { w: W, h: H },
            disc: { cx: cx, cy: cy, r: r, px: px, notToScale: notToScale },
            phase: phase, rings: rings, mosaic: mosaic, roi: roi, viewBox: viewBox,
        };
    }

    function fit(telescope, camera, amp, body, diameterArcsec) {
        const m = metrics(telescope, camera, amp);
        const D = num(diameterArcsec);
        const A = ampOf(amp);
        // Missing data first; then an input that is not usable (the body's diameter, the
        // amplification, an overflowed focal ratio), or a result that is not finite, is
        // not computable.
        const value = (needs, inputs, make, keys) => {
            const code = why(telescope, camera, needs);
            if (code) return missing(code);
            if (inputs.some(x => num(x) === null)) return missing('notComputable');
            return finiteOr(make(), keys);
        };

        const disc = value(['focalLength', 'pixelSize'], [D, A], () => ({
            ok: true, px: D / m.scale,
            ringsPx: body === 'saturn' ? D * SATURN_RING_FACTOR / m.scale : null,
        }), ['px', 'ringsPx']);
        const fRatio = value(['focalLength', 'aperture'], [A],
            () => ({ ok: true, value: m.fRatio }), ['value']);
        const ideal = value(['pixelSize'], [],
            () => ({ ok: true, value: IDEAL_PER_UM * m.pixel }), ['value']);
        const status = value(['focalLength', 'aperture', 'pixelSize'], [A, m.fRatio, IDEAL_PER_UM * m.pixel],
            () => ({ ok: true, value: ratioStatus(m.fRatio, IDEAL_PER_UM * m.pixel) }), []);

        let detailValue = null;
        if (body !== 'moon') {
            detailValue = value(['aperture'], [D],
                () => Object.assign({ ok: true }, detail(D, m.aperture)), ['elements']);
        }
        let rotation = null;
        if (ROTATION_HOURS[body]) {
            rotation = value(['aperture'], [D],
                () => ({ ok: true, seconds: rotationSeconds(body, m.aperture, D) }), ['seconds']);
        }
        let mosaic = null;
        if (body === 'moon') {
            mosaic = value(['focalLength', 'sensorSize'], [D, A],
                () => Object.assign({ ok: true }, mosaicPanels(D, m.fovW, m.fovH, OVERLAP)), ['panels']);
        }

        return {
            disc: disc, fRatio: fRatio, ideal: ideal, status: status,
            detail: detailValue, rotation: rotation, mosaic: mosaic,
        };
    }

    return {
        IDEAL_PER_UM: IDEAL_PER_UM, AMPLIFICATIONS: AMPLIFICATIONS, ON_TARGET_BAND: ON_TARGET_BAND,
        DETAIL_FINE: DETAIL_FINE, DETAIL_MAIN: DETAIL_MAIN, OVERLAP: OVERLAP,
        SUMMARY_MIN_ALTITUDE: SUMMARY_MIN_ALTITUDE, SATURN_RING_INNER_FACTOR: SATURN_RING_INNER_FACTOR,
        PREVIEW_WIDTH: PREVIEW_WIDTH, PREVIEW_MIN_DISC: PREVIEW_MIN_DISC, ROI_MARGIN: ROI_MARGIN,
        ROI_SIZES: ROI_SIZES, PHASE_BODIES: PHASE_BODIES, PLAUSIBLE_SENSOR_MM: PLAUSIBLE_SENSOR_MM,
        PLAUSIBLE_SENSOR_PX: PLAUSIBLE_SENSOR_PX, plausibleSensor: plausibleSensor,
        ROTATION_HOURS: ROTATION_HOURS, SATURN_RING_FACTOR: SATURN_RING_FACTOR,
        metrics: metrics, ratioStatus: ratioStatus, detail: detail,
        bestAmplification: bestAmplification, defaultTelescope: defaultTelescope,
        defaultCamera: defaultCamera, rotationSeconds: rotationSeconds,
        mosaicPanels: mosaicPanels, fit: fit, summary: summary,
        phasePath: phasePath, roiFor: roiFor, preview: preview,
    };
});
