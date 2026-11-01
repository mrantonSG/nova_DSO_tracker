/*
 * nova_solar_rigfit.js — the Solar System page's Rig fit card, worked out in the browser.
 *
 * For the selected body and each of the user's active rigs: the disc in pixels, the
 * rig's focal ratio against the ideal planetary one, the Barlow that would get there,
 * and for Jupiter, Saturn and Mars the rotation limit, for the Moon the mosaic. No DOM:
 * the page (static/js/solar_system.js) renders the lines and formats the numbers.
 *
 * A rig is the page's embedded record (core.py _solar_rigs): the raw component fields
 * { name, has_telescope, has_camera, focal_length_mm, aperture_mm, factor,
 * pixel_size_um, sensor_width_mm, sensor_height_mm }, so each value is worked out from
 * the fields it needs and only that value is missing when one of them is.
 *
 * metrics(rig) follows _compute_rig_metrics_from_components (nova/helpers.py) formula
 * by formula, so the numbers match what Nova shows elsewhere:
 *   efl        = focal_length_mm x (factor, 1 without a reducer/extender or factor)
 *   fRatio     = efl / aperture_mm
 *   scale      = 206.265 x pixel_size_um / efl                      arcsec per pixel
 *   fovW, fovH = 2 atan(sensor / 2 / efl), in arcminutes
 * except that a missing aperture leaves the image scale and field of view alone (the
 * server's helper drops them all); only the focal ratio needs the aperture.
 *
 * fit(rig, body, diameterArcsec) -> { name, disc, fRatio, ideal, barlow, rotation, mosaic }.
 * Each value is { ok: true, ... } or { ok: false, missing: <code> }; rotation and mosaic
 * are null for the bodies they do not apply to. Missing codes, the first that applies:
 * 'noTelescope', 'noCamera', 'focalLength', 'factor' (a reducer/extender factor of 0 or
 * less: missing data, not 1), 'aperture', 'pixelSize', 'sensorSize'; then 'notComputable'
 * when the fields are there but a result is not a finite number (absurd values) or the
 * body's diameter is not positive.
 *   disc      { px, ringsPx (Saturn only, else null) }: diameter / scale
 *   fRatio    { value }: the rig's focal ratio
 *   ideal     { value }: IDEAL_PER_UM x pixel size (the common rule f ≈ 5 x pixel in µm)
 *   barlow    barlowFor(fRatio, ideal)
 *   rotation  { seconds }: rotationSeconds(body, aperture, diameter)
 *   mosaic    mosaicPanels(diameter, fovW, fovH, OVERLAP)
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
    const BARLOWS = Object.freeze([1.5, 2, 2.5, 3, 4, 5]);
    const NONE_BAND = 0.10;            // within 10 % of the ideal: no Barlow
    const OVERLAP = 0.20;
    // Sidereal rotation periods in hours (System III for Jupiter and Saturn)
    const ROTATION_HOURS = Object.freeze({ jupiter: 9.925, saturn: 10.56, mars: 24.62 });
    // Saturn's outer ring diameter over its equatorial diameter: the A ring's outer edge,
    // 136 775 km, over the equatorial radius, 60 268 km (NASA Saturn Fact Sheet) = 2.27.
    const SATURN_RING_FACTOR = 136775 / 60268;

    const DEG = Math.PI / 180;

    /** A positive finite number, or null. */
    function num(x) {
        return typeof x === 'number' && isFinite(x) && x > 0 ? x : null;
    }

    const missing = code => ({ ok: false, missing: code });

    /** The reducer/extender factor: 1 without one (or without a factor), null for 0 or less. */
    function factorOf(r) {
        return r.factor === null || r.factor === undefined ? 1.0 : num(r.factor);
    }

    /** `value` when each of its `keys` is a finite number (or null), else 'notComputable'. */
    function finiteOr(value, keys) {
        const bad = keys.some(k => value[k] !== null && !(typeof value[k] === 'number' && isFinite(value[k])));
        return bad ? missing('notComputable') : value;
    }

    function metrics(rig) {
        const r = rig || {};
        const fl = r.has_telescope ? num(r.focal_length_mm) : null;
        const ap = r.has_telescope ? num(r.aperture_mm) : null;
        const px = r.has_camera ? num(r.pixel_size_um) : null;
        const sw = r.has_camera ? num(r.sensor_width_mm) : null;
        const sh = r.has_camera ? num(r.sensor_height_mm) : null;
        const fac = factorOf(r);
        const efl = fl !== null && fac !== null ? fl * fac : null;
        const fov = s => (s !== null && efl !== null) ? 2 * Math.atan((s / 2.0) / efl) / DEG * 60.0 : null;
        return {
            efl: efl,
            fRatio: (efl !== null && ap !== null) ? efl / ap : null,
            scale: (efl !== null && px !== null) ? PLATE_SCALE * px / efl : null,
            fovW: fov(sw), fovH: fov(sh),
            aperture: ap, pixel: px,
        };
    }

    /** The first missing field among the telescope's and the camera's, in that order. */
    function why(rig, needs) {
        const r = rig || {};
        if (needs.indexOf('telescope') >= 0) {
            if (!r.has_telescope) return 'noTelescope';
        }
        if (needs.indexOf('camera') >= 0) {
            if (!r.has_camera) return 'noCamera';
        }
        if (needs.indexOf('focalLength') >= 0 && num(r.focal_length_mm) === null) return 'focalLength';
        if (needs.indexOf('factor') >= 0 && factorOf(r) === null) return 'factor';
        if (needs.indexOf('aperture') >= 0 && num(r.aperture_mm) === null) return 'aperture';
        if (needs.indexOf('pixelSize') >= 0 && num(r.pixel_size_um) === null) return 'pixelSize';
        if (needs.indexOf('sensorSize') >= 0
            && (num(r.sensor_width_mm) === null || num(r.sensor_height_mm) === null)) return 'sensorSize';
        return null;
    }

    /**
     * The Barlow from the rig's focal ratio to the ideal one, k = ideal / current:
     * { kind: 'above' } when k < 1 - NONE_BAND (already slower than ideal), 'none' within
     * NONE_BAND of 1, else 'factor' with the nearest of BARLOWS on a log scale (at most
     * the largest). `exact` is k.
     */
    function barlowFor(current, ideal) {
        const k = ideal / current;
        if (k < 1 - NONE_BAND) return { kind: 'above', exact: k };
        if (k <= 1 + NONE_BAND) return { kind: 'none', exact: k };
        let best = BARLOWS[0];
        for (const b of BARLOWS) if (Math.abs(Math.log(b / k)) < Math.abs(Math.log(best / k))) best = b;
        return { kind: 'factor', factor: best, exact: k };
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

    function fit(rig, body, diameterArcsec) {
        const r = rig || {};
        const m = metrics(r);
        const D = num(diameterArcsec);
        // Missing rig data first; then an input that is not a positive finite number (the
        // body's diameter, an overflowed focal ratio), or a result that is not finite, is
        // not computable.
        const value = (needs, inputs, make, keys) => {
            const code = why(r, needs);
            if (code) return missing(code);
            if (inputs.some(x => num(x) === null)) return missing('notComputable');
            return finiteOr(make(), keys);
        };

        const disc = value(['telescope', 'camera', 'focalLength', 'factor', 'pixelSize'], [D], () => ({
            ok: true, px: D / m.scale,
            ringsPx: body === 'saturn' ? D * SATURN_RING_FACTOR / m.scale : null,
        }), ['px', 'ringsPx']);
        const fRatio = value(['telescope', 'focalLength', 'factor', 'aperture'], [],
            () => ({ ok: true, value: m.fRatio }), ['value']);
        const ideal = value(['camera', 'pixelSize'], [],
            () => ({ ok: true, value: IDEAL_PER_UM * m.pixel }), ['value']);
        const barlow = value(['telescope', 'camera', 'focalLength', 'factor', 'aperture', 'pixelSize'], [m.fRatio],
            () => Object.assign({ ok: true }, barlowFor(m.fRatio, IDEAL_PER_UM * m.pixel)), ['exact']);

        let rotation = null;
        if (ROTATION_HOURS[body]) {
            rotation = value(['telescope', 'aperture'], [D],
                () => ({ ok: true, seconds: rotationSeconds(body, m.aperture, D) }), ['seconds']);
        }
        let mosaic = null;
        if (body === 'moon') {
            mosaic = value(['telescope', 'camera', 'focalLength', 'factor', 'sensorSize'], [D],
                () => Object.assign({ ok: true }, mosaicPanels(D, m.fovW, m.fovH, OVERLAP)), ['panels']);
        }

        return {
            name: String(r.name === undefined || r.name === null ? '' : r.name),
            disc: disc, fRatio: fRatio, ideal: ideal, barlow: barlow, rotation: rotation, mosaic: mosaic,
        };
    }

    return {
        IDEAL_PER_UM: IDEAL_PER_UM, BARLOWS: BARLOWS, OVERLAP: OVERLAP,
        ROTATION_HOURS: ROTATION_HOURS, SATURN_RING_FACTOR: SATURN_RING_FACTOR,
        metrics: metrics, barlowFor: barlowFor, rotationSeconds: rotationSeconds,
        mosaicPanels: mosaicPanels, fit: fit,
    };
});
