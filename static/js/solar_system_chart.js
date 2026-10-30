/*
 * solar_system_chart.js — the Solar System page's altitude chart for one body.
 *
 * SolarSystemChart.create(config) returns { show(view), destroy() }. Everything it needs
 * comes in config; it reads no page globals:
 *   canvas     the <canvas> to draw on
 *   tz         the location's IANA zone; locName its name (axis title)
 *   lat, lon   the location
 *   threshold  location.altitude_threshold; interval location.sampling_interval
 *   mask       location.horizon_mask, [[az, alt], ...] (may be empty)
 *   nowMs      () -> the instant the Now line marks (the page's simulated one in sim mode)
 *   i18n       the page's translated labels (solarI18n in templates/solar_system.html)
 *   locale     the page language, for Luxon's month names
 *
 * show({body, label, view, date, year, month}):
 *   view 'day'    one observing night, date 'YYYY-MM-DD' (noon to noon)
 *   view 'month'  one calendar month, year and month (1-12)
 *   view 'year'   the twelve months of `year`
 * The night is NovaPlotData.build() with the body's altAzAt from NovaPlanets, so the
 * curve is the body's place at every sample. Month and year plot, per day, the highest
 * altitude from that day's sunset to the next sunrise (NovaSolarRows.nightlyMax(), the
 * table's own night window), for the body and for the Moon: a planet is imaged in the
 * evening or the morning, so its altitude at midnight says little. show() throws on any
 * engine failure; the page shows its translated error.
 *
 * Ported from graph_view_chart.js (which cannot be loaded here): the Now line and its
 * 60 s updater, the sun-event lines, the night shading, the threshold / horizon-mask
 * dataset, the midnight-centred x axis, the month and year rendering and the theme
 * update through stylingUtils.onThemeChange. Every label is translated. Not ported:
 * weather, transit lines, skyglow, the secondary object. The separate Moon lines are
 * left out when the Moon is the body.
 *
 * Needs Chart.js with the Luxon adapter and the annotation plugin, NovaAstro, NovaTransit,
 * NovaPlanets, NovaPlotData, NovaDashboardRows and NovaSolarRows, all loaded first.
 */
(function (root, factory) {
    if (typeof module === 'object' && module.exports) {
        module.exports = factory();
    } else {
        root.SolarSystemChart = factory();
    }
})(typeof self !== 'undefined' ? self : this, function () {
    'use strict';

    const NOW_COLOR = 'rgba(255, 99, 132, 0.8)';
    const NOW_LABEL_COLOR = 'rgba(255, 99, 132, 1)';
    const SUN_LINES = ['sunsetLine', 'duskLine', 'dawnLine', 'sunriseLine'];

    function su() {
        return (typeof window !== 'undefined' && window.stylingUtils) || {};
    }

    function isDark() {
        const s = su();
        return !!(s.isDarkTheme && s.isDarkTheme());
    }

    function lineColor(i, fallback) {
        const s = su();
        return s.getChartLineColor ? s.getChartLineColor(i) : fallback;
    }

    function textColor(fallback) {
        const s = su();
        if (s.getColor) return s.getColor('--text-primary', fallback);
        if (s.getCssVar) return s.getCssVar('--text-primary', fallback);
        return fallback;
    }

    function maskFill() {
        const s = su();
        return s.getCssVarAsRgba ? s.getCssVarAsRgba('--chart-line-3', 'rgba(99, 110, 114, 0.3)', 0.3)
            : 'rgba(99, 110, 114, 0.3)';
    }

    function themeColors() {
        const dark = isDark();
        return {
            grid: dark ? 'rgba(150, 150, 150, 0.3)' : 'rgba(128, 128, 128, 0.5)',
            text: dark ? '#e0e0e0' : '#333',
            tick: dark ? '#b0b0b0' : '#666',
            label: dark ? '#e0e0e0' : '#222',
            labelBg: dark ? 'rgba(40,40,40,0.92)' : 'rgba(255,255,255,0.92)',
            labelBorder: dark ? 'rgba(100,100,100,0.5)' : 'rgba(0,0,0,0.15)',
            shade: dark ? 'rgba(50, 50, 50, 0.6)' : 'rgba(211, 211, 211, 0.7)',
        };
    }

    /** A sample stamp (ISO with offset) or a number as chart milliseconds. */
    function toMs(val, tz) {
        if (typeof val === 'number') return (val < 1e12 ? val * 1000 : val);
        if (typeof val === 'string') {
            const hasOffset = /[Zz]|[+\-]\d{2}:?\d{2}$/.test(val);
            if (hasOffset) return luxon.DateTime.fromISO(val).setZone(tz).toMillis();
            return luxon.DateTime.fromISO(val, {zone: tz}).toMillis();
        }
        return null;
    }

    function nowAnnotation(nowMs, i18n) {
        return {
            type: 'line',
            borderColor: NOW_COLOR,
            borderWidth: 3,
            borderDash: [10, 3],
            label: {
                display: true,
                content: i18n.now,
                position: 'start',
                rotation: 90,
                font: { size: 10, weight: 'bold' },
                color: NOW_LABEL_COLOR,
                backgroundColor: 'rgba(255, 255, 255, 0.7)'
            },
            xMin: nowMs,
            xMax: nowMs,
            xScaleID: 'x'
        };
    }

    /** Sunset, astronomical dusk, dawn and sunrise lines, as the graph view draws them. */
    function sunAnnotations(sunEvents, baseDt, nextDt, i18n) {
        const annotations = {};
        const c = themeColors();

        function wallTimeMs(dt, timeStr) {
            if (!timeStr || !timeStr.includes(':')) return null;
            const [h, m] = timeStr.split(':').map(Number);
            return dt.set({hour: h, minute: m, second: 0, millisecond: 0}).toMillis();
        }

        const sunsetTime = wallTimeMs(baseDt, sunEvents.current.sunset);
        let duskTime;
        const duskCurrent = wallTimeMs(baseDt, sunEvents.current.astronomical_dusk);
        if (duskCurrent && sunsetTime && duskCurrent < sunsetTime) {
            duskTime = wallTimeMs(nextDt, sunEvents.current.astronomical_dusk);
        } else {
            duskTime = duskCurrent;
        }
        const dawnTime = wallTimeMs(nextDt, sunEvents.next.astronomical_dawn);
        const sunriseTime = wallTimeMs(nextDt, sunEvents.next.sunrise);

        const labelConfig = {
            display: true,
            position: 'start',
            rotation: 90,
            font: {size: 10, weight: '400'},
            color: c.label,
            backgroundColor: c.labelBg,
            borderColor: c.labelBorder,
            borderWidth: 1
        };
        const line = (t, text) => ({
            type: 'line', xMin: t, xMax: t, borderColor: 'black', borderWidth: 1,
            label: Object.assign({}, labelConfig, {content: text})
        });

        if (sunsetTime) annotations.sunsetLine = line(sunsetTime, i18n.sunset);
        if (duskTime) annotations.duskLine = line(duskTime, i18n.astroDusk);
        if (dawnTime) annotations.dawnLine = line(dawnTime, i18n.astroDawn);
        if (sunriseTime) annotations.sunriseLine = line(sunriseTime, i18n.sunrise);

        return { annotations: annotations, duskTime: duskTime, dawnTime: dawnTime };
    }

    function nightShadePlugin(duskTime, dawnTime) {
        return {
            id: 'nightShade',
            beforeDraw(chart) {
                const {ctx, chartArea, scales} = chart;
                if (!chartArea) return;
                const left = scales.x.getPixelForValue(scales.x.min);
                const right = scales.x.getPixelForValue(scales.x.max);
                const duskPx = duskTime ? scales.x.getPixelForValue(duskTime) : right;
                const dawnPx = dawnTime ? scales.x.getPixelForValue(dawnTime) : left;
                ctx.save();
                ctx.fillStyle = themeColors().shade;
                if (duskPx < dawnPx) {
                    if (duskPx > left) ctx.fillRect(left, chartArea.top, duskPx - left, chartArea.height);
                    if (dawnPx < right) ctx.fillRect(dawnPx, chartArea.top, right - dawnPx, chartArea.height);
                } else {
                    ctx.fillRect(left, chartArea.top, right - left, chartArea.height);
                }
                ctx.restore();
            }
        };
    }

    /**
     * An azimuth series as {x, y} points, with a null point between two samples that
     * wrap across 360/0, so the dashed line breaks there instead of drawing a vertical
     * connector the height of the chart. (graph_view_chart.js passes its azimuth series
     * through unchanged and draws that connector.)
     */
    function azimuthPoints(labels, values) {
        const out = [];
        for (let k = 0; k < values.length; k++) {
            const y = values[k], prev = k > 0 ? values[k - 1] : null;
            if (y !== null && prev !== null && Math.abs(y - prev) > 180) {
                out.push({x: (labels[k - 1] + labels[k]) / 2, y: null});
            }
            out.push({x: labels[k], y: y});
        }
        return out;
    }

    /** Dataset colours by role, so a theme change can repaint them. */
    function colorFor(role) {
        switch (role) {
            case 'body': case 'bodyAz': return lineColor(0, '#36A2EB');
            case 'moon': case 'moonAz': return lineColor(1, '#FFC107');
            case 'mask': return lineColor(2, '#636e72');
            case 'horizon': return textColor('#333');
        }
        return undefined;
    }

    function create(config) {
        const cfg = config || {};
        const canvas = cfg.canvas;
        if (!canvas) throw new Error('solar_system_chart: no canvas');
        const tz = String(cfg.tz);
        const i18n = cfg.i18n || {};
        const nowFn = typeof cfg.nowMs === 'function' ? cfg.nowMs : () => Date.now();

        let chart = null;
        let nowTimer = null;
        let unsubscribe = null;

        function stopNow() {
            if (nowTimer) clearInterval(nowTimer);
            nowTimer = null;
        }

        function clear() {
            stopNow();
            if (chart) chart.destroy();
            chart = null;
        }

        function startNow() {
            stopNow();
            const update = () => {
                const ann = chart && chart.options && chart.options.plugins
                    && chart.options.plugins.annotation && chart.options.plugins.annotation.annotations;
                if (!ann || !ann.currentTimeLine) { stopNow(); return; }
                const t = nowFn();
                ann.currentTimeLine.xMin = t;
                ann.currentTimeLine.xMax = t;
                chart.update('none');
            };
            update();
            nowTimer = setInterval(update, 60000);
        }

        function altAzAt(body) {
            const lat = +cfg.lat, lon = +cfg.lon;
            return t => NovaPlanets.altAz(body, t, lat, lon);
        }

        function dataset(role, label, data, extra) {
            return Object.assign({
                label: label, data: data, borderColor: colorFor(role), pointRadius: 0, _role: role
            }, extra || {});
        }

        function showDay(v) {
            const data = NovaPlotData.build({
                date: v.date, lat: cfg.lat, lon: cfg.lon, tz: tz,
                threshold: cfg.threshold, interval: cfg.interval, mask: cfg.mask, skyglow: null,
                altAzAt: altAzAt(v.body)
            });
            const labels = data.times.map(val => toMs(val, tz));
            const baseDt = luxon.DateTime.fromISO(data.date, {zone: tz});
            const nextDt = baseDt.plus({days: 1});

            const annotations = { currentTimeLine: nowAnnotation(nowFn(), i18n) };
            const sun = sunAnnotations(data.sun_events, baseDt, nextDt, i18n);
            Object.assign(annotations, sun.annotations);

            // The x axis centred on local midnight, as the graph view centres it.
            const firstMs = labels[0], lastMs = labels[labels.length - 1];
            const span = lastMs - firstMs;
            const midnightMs = nextDt.startOf('day').toMillis();
            const xMin = firstMs + (midnightMs - (firstMs + lastMs) / 2);
            const xMax = xMin + span;

            const isMoon = v.body === 'moon';
            const line = {yAxisID: 'yAltitude', borderWidth: 4, tension: 0.1};
            const az = {yAxisID: 'yAzimuth', borderDash: [5, 5], borderWidth: 3.5, tension: 0.1};
            const datasets = [
                dataset('body', v.label + ' – ' + i18n.altitude, data.object_alt, line),
                isMoon ? null : dataset('moon', i18n.moon + ' – ' + i18n.altitude, data.moon_alt, line),
                dataset('mask', i18n.horizonMask, data.horizon_mask_alt,
                        {yAxisID: 'yAltitude', borderWidth: 2, tension: 0.1, fill: 'start', backgroundColor: maskFill()}),
                dataset('horizon', i18n.horizon, Array(labels.length).fill(0), {yAxisID: 'yAltitude', borderWidth: 2}),
                dataset('bodyAz', v.label + ' – ' + i18n.azimuth, azimuthPoints(labels, data.object_az), az),
                isMoon ? null : dataset('moonAz', i18n.moon + ' – ' + i18n.azimuth, azimuthPoints(labels, data.moon_az), az),
            ].filter(Boolean);

            const c = themeColors();
            clear();
            chart = new Chart(canvas.getContext('2d'), {
                type: 'line',
                data: { labels: labels, datasets: datasets },
                plugins: [nightShadePlugin(sun.duskTime, sun.dawnTime)],
                options: {
                    // The size is the card's (#chart-area in solar_system.css): 2:1, capped.
                    responsive: true,
                    maintainAspectRatio: false,
                    adapters: {date: {zone: tz}},
                    layout: { padding: { top: 46 } },
                    plugins: {
                        annotation: {annotations: annotations},
                        legend: {position: 'bottom', labels: {color: c.tick}}
                    },
                    scales: {
                        x: {
                            type: 'time',
                            adapters: {date: {zone: tz, locale: cfg.locale}},
                            parsing: false,
                            time: {unit: 'hour', displayFormats: {hour: 'HH:mm'}},
                            min: xMin,
                            max: xMax,
                            bounds: 'ticks',
                            ticks: {source: 'auto', color: c.tick},
                            grid: {color: c.grid, borderDash: [2, 2]},
                            title: {display: true, text: i18n.localTime + ' – ' + cfg.locName, color: c.text}
                        },
                        yAltitude: {
                            position: 'left', min: -90, max: 90,
                            ticks: {color: c.tick},
                            title: {display: true, text: i18n.altitudeAxis, color: c.text},
                            grid: {color: c.grid, borderDash: [2, 2]}
                        },
                        yAzimuth: {
                            position: 'right', min: 0, max: 360,
                            ticks: {color: c.tick},
                            title: {display: true, text: i18n.azimuthAxis, color: c.text},
                            grid: {drawOnChartArea: false}
                        }
                    }
                }
            });
            startNow();
        }

        function showRange(v) {
            const year = +v.year;
            const months = v.view === 'year' ? [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12] : [+v.month];
            const isMoon = v.body === 'moon';
            const inputs = {location: {
                lat: cfg.lat, lon: cfg.lon, timezone: tz,
                altitude_threshold: cfg.threshold, sampling_interval: cfg.interval
            }};
            const series = body => months.map(m => NovaSolarRows.nightlyMax({
                inputs: inputs, body: body, year: year, month: m
            }));
            const bodyBlocks = series(v.body);
            const moonBlocks = isMoon ? null : series('moon');
            const bodyAlt = [], moonAlt = [], horizon = [];
            bodyBlocks.forEach((block, b) => block.dates.forEach((d, k) => {
                const t = d + 'T00:00:00';
                bodyAlt.push({x: t, y: block.max_alt[k]});
                if (moonBlocks) moonAlt.push({x: t, y: moonBlocks[b].max_alt[k]});
                horizon.push({x: t, y: 0});
            }));

            const isYear = v.view === 'year';
            const xTitle = isYear ? i18n.monthAxis + ' – ' + year
                : i18n.dayAxis + ' – ' + year + '-' + String(v.month).padStart(2, '0');
            const c = themeColors();
            const datasets = [
                dataset('body', v.label, bodyAlt, {borderWidth: 3, tension: 0.2}),
                isMoon ? null : dataset('moon', i18n.moon, moonAlt, {borderWidth: 2.5, tension: 0.0}),
                dataset('horizon', i18n.horizon, horizon, {borderWidth: 2}),
            ].filter(Boolean);

            clear();
            chart = new Chart(canvas.getContext('2d'), {
                type: 'line',
                data: { datasets: datasets },
                options: {
                    adapters: {date: {zone: tz}},
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        x: {
                            type: 'time',
                            adapters: {date: {zone: tz, locale: cfg.locale}},
                            time: {unit: isYear ? 'month' : 'day', displayFormats: isYear ? {month: 'MMM'} : {day: 'dd'}},
                            grid: {color: c.grid},
                            ticks: {color: c.tick},
                            title: {display: true, text: xTitle, color: c.text}
                        },
                        y: {
                            min: -90, max: 90,
                            grid: {color: c.grid},
                            ticks: {color: c.tick},
                            title: {display: true, text: i18n.nightlyMaxAxis, color: c.text}
                        }
                    },
                    plugins: { legend: {position: 'bottom', labels: {color: c.tick}} }
                }
            });
        }

        /** Repaint for the current theme, as graph_view_chart.js's updateChartForTheme(). */
        function applyTheme() {
            if (!chart) return;
            const c = themeColors();
            const scales = chart.options.scales || {};
            Object.keys(scales).forEach(k => {
                const s = scales[k];
                if (s.grid && s.grid.drawOnChartArea !== false) s.grid.color = c.grid;
                if (s.ticks) s.ticks.color = c.tick;
                if (s.title) s.title.color = c.text;
            });
            const legend = chart.options.plugins && chart.options.plugins.legend;
            if (legend && legend.labels) legend.labels.color = c.tick;
            const ann = chart.options.plugins && chart.options.plugins.annotation
                && chart.options.plugins.annotation.annotations;
            if (ann) {
                // Only the sun-event lines are themed; the Now line keeps its own colour.
                SUN_LINES.forEach(key => {
                    if (ann[key] && ann[key].label) {
                        ann[key].label.color = c.label;
                        ann[key].label.backgroundColor = c.labelBg;
                        ann[key].label.borderColor = c.labelBorder;
                    }
                });
            }
            chart.data.datasets.forEach(ds => {
                const color = colorFor(ds._role);
                if (color) ds.borderColor = color;
                if (ds._role === 'mask') ds.backgroundColor = maskFill();
            });
            chart.update('none');
        }

        const s = su();
        if (s.onThemeChange) unsubscribe = s.onThemeChange(applyTheme);

        return {
            show(v) {
                if (!v || !v.body) throw new Error('solar_system_chart: no body');
                if (v.view === 'month' || v.view === 'year') showRange(v);
                else showDay(v);
            },
            destroy() {
                clear();
                if (typeof unsubscribe === 'function') unsubscribe();
                unsubscribe = null;
            }
        };
    }

    return { create: create };
});
