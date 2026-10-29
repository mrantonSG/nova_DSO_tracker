/*
 * nova_weather_icons.js — the icons of the weather rows over the Day chart, as data.
 *
 * One dependency-free file, shared by the iOS app (inline SVG, src/detail_chart.js) and
 * Nova (to be drawn on a canvas). It holds no DOM code: each icon is a viewBox and a
 * list of shapes, every one an SVG path string, which an <svg> takes as a <path d> and
 * a canvas as new Path2D(d).
 *
 *   cloudIcon(cloudcover)   the icon id for a cloud cover level 1–9, by Nova's words:
 *                           1 Clear, 2–4 P. Clear, 5–6 P. Cloudy, 7–8 Cloudy,
 *                           9 Overcast. Null for a level Nova has no word for.
 *   seeingIcon(seeing)      the icon id for a seeing level 1–8: 1 Exc, 2–3 Good,
 *                           4–5 Avg, 6–7 Poor, 8 Bad. Null for any other value,
 *                           7timer's -9999 included.
 *   ICONS[id]               { viewBox, shapes }.
 *
 * The shapes are drawn in order, front last. Each is one of
 *   { d, fill, stroke, strokeWidth, lineJoin, opacity }
 *       a path. fill and stroke are 'none' or 'currentColor' — the colour the caller
 *       draws in (on a canvas, the fillStyle/strokeStyle it sets); strokeWidth is in
 *       viewBox units.
 *   { d, cut: true, strokeWidth }
 *       a cutout: it erases what the shapes before it drew, inside d and up to half of
 *       strokeWidth outside its edge — the gap that keeps a cloud clear of the Moon or
 *       of the cloud behind it. On a canvas: globalCompositeOperation
 *       'destination-out', then fill(path) and stroke(path) at that width, then back to
 *       'source-over'. It erases everything under it, so draw each icon on a canvas or
 *       layer of its own and composite that. In SVG: a mask over the shapes before it,
 *       white with the path filled and stroked in black.
 *
 * Every size is for a 16-unit square shown at 14 CSS px, the app's size: the strokes are
 * 1.3 px there. Drawn larger or smaller they scale with the icon.
 */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.NovaWeatherIcons = factory();
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const VIEWBOX = '0 0 16 16';

    // One CSS px of the 14 px icon, in viewBox units.
    const PX = 16 / 14;
    const STROKE = 1.3 * PX;
    const CUT = 2.6 * PX;

    // Nova's words for the levels (NovaWeather's CLOUD and SEEING), and the icon of each.
    const CLOUD_ICON = { 1: 'clear', 2: 'partlyClear', 3: 'partlyClear', 4: 'partlyClear',
        5: 'partlyCloudy', 6: 'partlyCloudy', 7: 'cloudy', 8: 'cloudy', 9: 'overcast' };
    const SEEING_ICON = { 1: 'seeingExc', 2: 'seeingGood', 3: 'seeingGood', 4: 'seeingAvg',
        5: 'seeingAvg', 6: 'seeingPoor', 7: 'seeingPoor', 8: 'seeingBad' };

    const has = (map, key) => Object.prototype.hasOwnProperty.call(map, key);

    function cloudIcon(cloudcover) {
        return has(CLOUD_ICON, cloudcover) ? CLOUD_ICON[cloudcover] : null;
    }

    function seeingIcon(seeing) {
        return has(SEEING_ICON, seeing) ? SEEING_ICON[seeing] : null;
    }

    // ------------------------------------------------------------------ clouds

    // The Moon is a crescent biting its top right; the cloud has its base at y 12.5:
    //   MOON  M9.5 2.7A5.5 5.5 0 1 0 13.3 10.5 4.4 4.4 0 0 1 9.5 2.7Z
    //   CLOUD M4.5 12.5h7a2.75 2.75 0 0 0 .2-5.5 3.6 3.6 0 0 0-6.9.9 2.3 2.3 0 0 0-.3 4.6Z
    // The paths below are those two moved and scaled, the transform written into the
    // numbers so the stroke keeps its width: each comment says which and how.
    const outline = (d) => ({ d: d, fill: 'none', stroke: 'currentColor', strokeWidth: STROKE,
        lineJoin: 'round', opacity: 1 });
    const cut = (d) => ({ d: d, cut: true, strokeWidth: CUT });

    // MOON translate(-1.2 -1.2) scale(0.9)
    const MOON_PARTLY_CLEAR = 'M7.35 1.23A4.95 4.95 0 1 0 10.77 8.25 3.96 3.96 0 0 1 7.35 1.23Z';
    // CLOUD translate(6.6 6.2) scale(0.6)
    const CLOUD_PARTLY_CLEAR = 'M9.3 13.7h4.2a1.65 1.65 0 0 0 0.12 -3.3 2.16 2.16 0 0 0 -4.14 0.54' +
        ' 1.38 1.38 0 0 0 -0.18 2.76Z';
    // MOON translate(-1.6 -2) scale(0.85)
    const MOON_PARTLY_CLOUDY = 'M6.475 0.295A4.675 4.675 0 1 0 9.705 6.925 3.74 3.74 0 0 1 6.475 0.295Z';
    // CLOUD translate(2.6 2.4) scale(0.86)
    const CLOUD_PARTLY_CLOUDY = 'M6.47 13.15h6.02a2.365 2.365 0 0 0 0.172 -4.73 3.096 3.096 0 0 0' +
        ' -5.934 0.774 1.978 1.978 0 0 0 -0.258 3.956Z';
    // CLOUD translate(0 0.4)
    const CLOUD_CLOUDY = 'M4.5 12.9h7a2.75 2.75 0 0 0 0.2 -5.5 3.6 3.6 0 0 0 -6.9 0.9 2.3 2.3 0 0 0' +
        ' -0.3 4.6Z';
    // CLOUD translate(5 -1.5) scale(0.68), the one behind
    const CLOUD_OVERCAST_BACK = 'M8.06 7h4.76a1.87 1.87 0 0 0 0.136 -3.74 2.448 2.448 0 0 0 -4.692 0.612' +
        ' 1.564 1.564 0 0 0 -0.204 3.128Z';
    // CLOUD translate(-0.6 1.6) scale(0.86), the one in front
    const CLOUD_OVERCAST_FRONT = 'M3.27 12.35h6.02a2.365 2.365 0 0 0 0.172 -4.73 3.096 3.096 0 0 0' +
        ' -5.934 0.774 1.978 1.978 0 0 0 -0.258 3.956Z';

    const ICONS = {
        clear: { viewBox: VIEWBOX, shapes: [
            outline('M9.5 2.7A5.5 5.5 0 1 0 13.3 10.5 4.4 4.4 0 0 1 9.5 2.7Z')] },
        partlyClear: { viewBox: VIEWBOX, shapes: [
            outline(MOON_PARTLY_CLEAR), cut(CLOUD_PARTLY_CLEAR), outline(CLOUD_PARTLY_CLEAR)] },
        partlyCloudy: { viewBox: VIEWBOX, shapes: [
            outline(MOON_PARTLY_CLOUDY), cut(CLOUD_PARTLY_CLOUDY), outline(CLOUD_PARTLY_CLOUDY)] },
        cloudy: { viewBox: VIEWBOX, shapes: [outline(CLOUD_CLOUDY)] },
        overcast: { viewBox: VIEWBOX, shapes: [
            outline(CLOUD_OVERCAST_BACK), cut(CLOUD_OVERCAST_FRONT), outline(CLOUD_OVERCAST_FRONT)] },
    };

    // ------------------------------------------------------------------ seeing

    // Signal strength: Exc four bars, Good three, Avg two, Poor one, Bad one faded. Bar i
    // stands on y 13.5, 2.4 wide and 4.5 + 2.5i high, at x 2 + 3.4i, its corners
    // rounded by 0.5.
    function bar(i, opacity) {
        const x = 2 + i * 3.4, h = 4.5 + i * 2.5, y = 13.5 - h, w = 2.4, r = 0.5;
        const n = (v) => String(Math.round(v * 1e4) / 1e4);
        const arc = (dx, dy) => 'a' + r + ' ' + r + ' 0 0 1 ' + n(dx) + ' ' + n(dy);
        return { d: 'M' + n(x + r) + ' ' + n(y) + 'h' + n(w - 2 * r) + arc(r, r) + 'v' + n(h - 2 * r) +
            arc(-r, r) + 'h' + n(-(w - 2 * r)) + arc(-r, -r) + 'v' + n(-(h - 2 * r)) + arc(r, -r) + 'Z',
            fill: 'currentColor', stroke: 'none', strokeWidth: 0, lineJoin: 'miter', opacity: opacity };
    }

    const bars = (count, opacity) => {
        const out = [];
        for (let i = 0; i < count; i++) out.push(bar(i, opacity));
        return { viewBox: VIEWBOX, shapes: out };
    };

    ICONS.seeingExc = bars(4, 1);
    ICONS.seeingGood = bars(3, 1);
    ICONS.seeingAvg = bars(2, 1);
    ICONS.seeingPoor = bars(1, 1);
    ICONS.seeingBad = bars(1, 0.4);

    return {
        cloudIcon: cloudIcon,
        seeingIcon: seeingIcon,
        ICONS: ICONS,
    };
});
