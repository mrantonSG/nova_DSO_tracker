(function() {
    'use strict';

    // ==========================================================================
    // FRAMING CONFIGURATION CONSTANTS (imported from framing-utils)
    // ==========================================================================
    const FRAMING_CONFIG = window.framingUtils?.CONSTANTS || {
        DEFAULT_FOV_DEG: 1.5,
        ROTATION_MIN_DEG: 0,
        ROTATION_MAX_DEG: 360,
        WEATHER_GROUPING_MS: 10800000
    };

    let currentTimeUpdateInterval = null;

    // Helper to get framing utils (with fallback for missing module)
    const fu = () => window.framingUtils || {
        CONSTANTS: FRAMING_CONFIG,
        normalizeAngle: (deg) => ((deg % 360) + 360) % 360,
        arcminToDeg: (arcmin) => arcmin / 60,
        degToRad: (deg) => deg * Math.PI / 180,
        radToDeg: (rad) => rad * 180 / Math.PI,
        parseFramingQueryString: (qs) => {
            const q = new URLSearchParams(qs || '');
            return {
                rig: q.get('rig'),
                ra: parseFloat(q.get('ra')) || null,
                dec: parseFloat(q.get('dec')) || null,
                rot: parseFloat(q.get('rot')) || null,
                survey: q.get('survey'),
                blend: q.get('blend'),
                blendOp: parseFloat(q.get('blend_op')) || null,
                m_cols: q.get('m_cols'),
                m_rows: q.get('m_rows'),
                m_ov: q.get('m_ov'),
                img_brightness: parseFloat(q.get('img_b')) || null,
                img_contrast: parseFloat(q.get('img_c')) || null,
                img_gamma: parseFloat(q.get('img_g')) || null,
                img_saturation: parseFloat(q.get('img_s')) || null,
                geo_belt_enabled: q.get('geo_belt') === '1' ? true : null
            };
        },
        calculateNudgedCenter: (center, dxArcmin, dyArcmin) => {
            const dxDeg = dxArcmin / 60;
            const dyDeg = dyArcmin / 60;
            const decRad = center.dec * Math.PI / 180;
            let newRa = center.ra;
            if (Math.abs(decRad) < (Math.PI / 2.0 - 0.001)) {
                newRa -= dxDeg / Math.cos(decRad);
            }
            return { ra: ((newRa % 360) + 360) % 360, dec: center.dec + dyDeg };
        },
        calculateGeoBeltDeclination: (observerLatDeg) => {
            const latRad = observerLatDeg * Math.PI / 180;
            const Re = 6378, Rgeo = 42164;
            const num = Re * Math.sin(latRad);
            const den = Rgeo - (Re * Math.cos(latRad));
            const parallaxRad = Math.atan2(num, den);
            return -(parallaxRad * 180 / Math.PI);
        },
        generateGeoBeltRaPoints: () => {
            const points = [];
            for (let ra = 0; ra < 360; ra += 0.2) points.push(ra);
            return points;
        },
        formatRaCsv: (raDeg) => {
            const hours = raDeg / 15;
            const h = Math.floor(hours);
            const m = Math.round((hours - h) * 60);
            return `${h}h ${m}m`;
        },
        formatDecCsv: (decDeg) => {
            const sign = decDeg >= 0 ? '+' : '-';
            const absDec = Math.abs(decDeg);
            const d = Math.floor(absDec);
            const m = Math.round((absDec - d) * 60);
            return `${sign}${d}° ${m}m`;
        }
    };

    // ==========================================================================
    // HiPS SURVEY ID → URL MAP (bypasses MocServer lookup)
    // ==========================================================================
    const HIPS_ID_TO_URL = Object.freeze({
        "P/DSS2/color":                     "https://alasky.cds.unistra.fr/DSS/DSSColor",
        "P/allWISE/color":                  "https://alaskybis.cds.unistra.fr/AllWISE/RGB-W4-W2-W1",
        "P/2MASS/color":                    "https://alaskybis.cds.unistra.fr/2MASS/Color",
        "P/GALEXGR6/AIS/color":             "https://alasky.cds.unistra.fr/GALEX/GR6-03-2014/AIS-Color",
        "P/PanSTARRS/DR1/color-z-zg-g":     "https://alasky.cds.unistra.fr/Pan-STARRS/DR1/color-z-zg-g",
        "P/SDSS9/color":                    "https://alasky.cds.unistra.fr/SDSS/DR9/color",
        "P/DSS2/red":                       "https://alasky.cds.unistra.fr/DSS/DSS2Merged",
    });

    // Offline view: when on, every /hips/ request from this modal goes to the
    // cache-only /hips/offline/ routes (never upstream). Auto-on only when the
    // browser reports it is offline; a survey that fails to load is not a
    // connectivity fact and must not switch it. The user's own toggle is
    // remembered and restored when connectivity returns.
    let offlineViewOn = false;
    let offlineViewUserSetting = null;

    function resolveHipsId(idOrUrl) {
        if (typeof idOrUrl !== "string") return idOrUrl;
        // Offline sky on: a whitelisted survey resolves to the same-origin proxy
        // root; the server does the CDS/mirror failover, so no direct URL here.
        if (window.NOVA_OFFLINE_SKY) {
            const local = window.framingUtils && window.framingUtils.localHipsRoot
                ? window.framingUtils.localHipsRoot(idOrUrl, window.NOVA_OFFLINE_SKY_SURVEYS) : null;
            if (local) {
                // Aladin treats a bare relative path as a HiPS ID and resolves it
                // via MocServer (a network call that hangs offline), so the local
                // root must always be an absolute URL.
                const root = offlineViewOn
                    ? window.framingUtils.offlineHipsRoot(local) : local;
                return window.framingUtils.absoluteHipsRoot(root);
            }
        }
        if (idOrUrl.startsWith("http")) return idOrUrl;
        return HIPS_ID_TO_URL[idOrUrl] || idOrUrl;
    }

    // --- CDS reachability (alasky/alaskybis host every survey except NSNS) ---
    const CDS_PROBE_TIMEOUT_MS = 4000;
    let cdsProbePromise = null;   // memoised for the page session; dropped after a failure
    let cdsReachable = true;      // result of the probe awaited by the current framing open
    let dssMirrorPromise = null;  // memoised like cdsProbePromise
    let dssMirror = null;         // DSS2 color mirror URL that answered, used while CDS is unreachable

    // true when <baseUrl>/properties answers 2xx within CDS_PROBE_TIMEOUT_MS
    function probeHips(baseUrl) {
        const ctrl = new AbortController();
        const timer = setTimeout(() => ctrl.abort(), CDS_PROBE_TIMEOUT_MS);
        return fetch(baseUrl + '/properties', { signal: ctrl.signal, cache: 'no-store' })
            .then(r => r.ok)
            .catch(() => false)
            .finally(() => clearTimeout(timer));
    }

    // A failed CDS probe is remembered for 10 minutes across reloads (sessionStorage);
    // meanwhile the CDS probe is skipped. Works without storage (private mode, blocked).
    const CDS_OUTAGE_KEY = 'nova.framing.cdsOutageUntil';
    const CDS_OUTAGE_MS = 10 * 60 * 1000;
    function cdsOutageRemembered() {
        try { return Number(sessionStorage.getItem(CDS_OUTAGE_KEY)) > Date.now(); } catch (e) { return false; }
    }
    function rememberCdsOutage(down) {
        try {
            if (down) sessionStorage.setItem(CDS_OUTAGE_KEY, String(Date.now() + CDS_OUTAGE_MS));
            else sessionStorage.removeItem(CDS_OUTAGE_KEY);
        } catch (e) { /* no storage: nothing remembered */ }
    }

    function probeCds() {
        if (cdsProbePromise) return cdsProbePromise;
        const p = probeHips(HIPS_ID_TO_URL['P/DSS2/color']);
        cdsProbePromise = p;
        p.then(ok => {
            rememberCdsOutage(!ok);
            if (!ok && cdsProbePromise === p) cdsProbePromise = null;   // next open probes again
        });
        return p;
    }

    // Race CDS against the DSS mirrors (rules in framingUtils.decideSurveyProbe).
    // Resolves to {cdsReachable, dssMirror}. A probe still running when the race is
    // decided keeps going in the background, so its result is memoised/remembered.
    function probeSurveySources() {
        const fu = window.framingUtils;
        const st = { cds: cdsOutageRemembered() ? 'skipped' : 'pending', mirror: 'idle', mirrorUrl: null, graceElapsed: false };
        return new Promise(resolve => {
            let done = false;
            let graceTimer = null;
            const step = () => {
                if (done) return;
                const r = fu.decideSurveyProbe(st);
                if (r.startMirror) {
                    st.mirror = 'pending';
                    probeDssMirror().then(url => { st.mirror = url ? 'ok' : 'fail'; st.mirrorUrl = url; step(); });
                }
                if (r.decision) {
                    done = true;
                    clearTimeout(graceTimer);
                    resolve(r.decision);
                }
            };
            if (st.cds === 'pending') probeCds().then(ok => { st.cds = ok ? 'ok' : 'fail'; step(); });
            graceTimer = setTimeout(() => { st.graceElapsed = true; step(); }, fu.PROBE_GRACE_MS);
            step();
        });
    }

    // First DSS2 color mirror (framingUtils.DSS_MIRRORS order) that answers, or null
    function probeDssMirror() {
        if (dssMirrorPromise) return dssMirrorPromise;
        const p = (async () => {
            for (const url of window.framingUtils.DSS_MIRRORS) {
                if (await probeHips(url)) return url;
            }
            return null;
        })();
        dssMirrorPromise = p;
        p.then(url => { if (!url && dssMirrorPromise === p) dssMirrorPromise = null; });   // next open probes again
        return p;
    }

    // Label of the option for a displayed survey (a DSS mirror shows as DSS2 color):
    // #survey-select first, then #blend-survey-select, which offers surveys the base
    // select does not (DSS2 red).
    function surveyOptionLabel(value) {
        const v = window.framingUtils.DSS_MIRRORS.includes(value) ? 'P/DSS2/color' : value;
        const opt = document.querySelector(`#survey-select option[value='${v}']`)
            || document.querySelector(`#blend-survey-select option[value='${v}']`);
        // Prefer the original label; the offline marker ("· offline") is appended
        // to the option text and must not leak into Aladin's layer name.
        return opt?.dataset?.origLabel || opt?.textContent;
    }

    // Frame size for the NSNS coverage check: the selected rig's diagonal, in degrees
    function framingFovDeg() {
        const sel = document.getElementById('framing-rig-select');
        const opt = sel && sel.selectedIndex >= 0 ? sel.options[sel.selectedIndex] : null;
        const w = parseFloat(opt?.dataset.fovw), h = parseFloat(opt?.dataset.fovh);
        return (w > 0 && h > 0) ? Math.hypot(w, h) / 60 : FRAMING_CONFIG.DEFAULT_FOV_DEG;
    }

    // The survey to display for a requested select/saved value; see framingUtils.chooseBaseSurvey
    function chooseDisplayedBase(value) {
        // Offline sky on: the proxy serves the requested survey and fails over to
        // the mirrors itself. Keep the requested value (so labels and credits stay
        // keyed on it); only the tile URL becomes local via resolveHipsId. The
        // "unreachable" notice still reflects the CDS probe.
        if (window.NOVA_OFFLINE_SKY && window.NOVA_OFFLINE_SKY_SURVEYS
                && Object.prototype.hasOwnProperty.call(window.NOVA_OFFLINE_SKY_SURVEYS, value)) {
            return { survey: value, notice: !cdsReachable };
        }
        const known = typeof value === 'string' && (value.startsWith('http') || Object.prototype.hasOwnProperty.call(HIPS_ID_TO_URL, value));
        return window.framingUtils.chooseBaseSurvey({
            requested: value,
            known,
            requestedOnCds: known && window.framingUtils.isCdsUrl(resolveHipsId(value)),
            cdsReachable,
            dssMirror,
            decDeg: window.NOVA_GRAPH_DATA?.objectDECDeg,
            fovDeg: framingFovDeg()
        });
    }

    // --- Nova Precession Helpers (J2000 <-> JNow) ---
    function getPrecessionMatrix(jd) {
        const T = (jd - 2451545.0) / 36525.0;
        const zeta = (2306.2181 * T + 0.30188 * T * T + 0.017998 * T * T * T) * (Math.PI / 180 / 3600);
        const z    = (2306.2181 * T + 1.09468 * T * T + 0.018203 * T * T * T) * (Math.PI / 180 / 3600);
        const theta= (2004.3109 * T - 0.42665 * T * T - 0.041833 * T * T * T) * (Math.PI / 180 / 3600);
    
        const cosZ = Math.cos(z), sinZ = Math.sin(z);
        const cosTh = Math.cos(theta), sinTh = Math.sin(theta);
        const cosZe = Math.cos(zeta), sinZe = Math.sin(zeta);
    
        return [
            [ cosZe * cosTh * cosZ - sinZe * sinZ, -sinZe * cosTh * cosZ - cosZe * sinZ, -sinTh * cosZ ],
            [ cosZe * cosTh * sinZ + sinZe * cosZ, -sinZe * cosTh * sinZ + cosZe * cosZ, -sinTh * sinZ ],
            [ cosZe * sinTh, -sinZe * sinTh, cosTh ]
        ];
    }
    
    function applyMatrix(m, v) {
        return [
            m[0][0]*v[0] + m[0][1]*v[1] + m[0][2]*v[2],
            m[1][0]*v[0] + m[1][1]*v[1] + m[1][2]*v[2],
            m[2][0]*v[0] + m[2][1]*v[1] + m[2][2]*v[2]
        ];
    }
    
    function convertJ2000ToJNow(raDeg, decDeg) {
        const jdNow = (new Date().getTime() / 86400000.0) + 2440587.5;
        const m = getPrecessionMatrix(jdNow);
        const raRad = raDeg * Math.PI / 180.0, decRad = decDeg * Math.PI / 180.0;
        const x = Math.cos(decRad) * Math.cos(raRad);
        const y = Math.cos(decRad) * Math.sin(raRad);
        const z = Math.sin(decRad);
        const v2 = applyMatrix(m, [x, y, z]);
        const r = Math.sqrt(v2[0]*v2[0] + v2[1]*v2[1]);
        let raNow = Math.atan2(v2[1], v2[0]);
        if (raNow < 0) raNow += 2 * Math.PI;
        const decNow = Math.atan2(v2[2], r);
        return { ra: raNow * 180.0 / Math.PI, dec: decNow * 180.0 / Math.PI };
    }
    
    // --- End Precession Helpers ---
    
    // Weather icons rendered off-screen, keyed by icon id + colour + devicePixelRatio
    const weatherIconCache = new Map();

    // Weather block colours when styling-utils.js is missing (its light-theme values)
    const WEATHER_FALLBACK_COLOURS = {
        cloud: {
            1: 'rgba(135, 206, 250, 0.15)',
            2: 'rgba(135, 206, 250, 0.25)',
            3: 'rgba(170, 170, 170, 0.2)',
            4: 'rgba(170, 170, 170, 0.3)',
            5: 'rgba(120, 120, 120, 0.35)',
            6: 'rgba(120, 120, 120, 0.45)',
            7: 'rgba(80, 80, 80, 0.5)',
            8: 'rgba(80, 80, 80, 0.6)',
            9: 'rgba(50, 50, 50, 0.7)'
        },
        seeing: {
            1: 'rgba(0, 255, 127, 0.2)',
            2: 'rgba(0, 255, 127, 0.3)',
            3: 'rgba(173, 255, 47, 0.3)',
            4: 'rgba(255, 255, 0, 0.3)',
            5: 'rgba(255, 215, 0, 0.3)',
            6: 'rgba(255, 165, 0, 0.3)',
            7: 'rgba(255, 69, 0, 0.3)',
            8: 'rgba(255, 0, 0, 0.3)'
        }
    };

    // A weather block's colour, read at draw time so it follows the theme
    const weatherColour = (kind, level) => window.stylingUtils && window.stylingUtils.getWeatherColor
        ? window.stylingUtils.getWeatherColor(level, kind)
        : WEATHER_FALLBACK_COLOURS[kind][level];

    // A CSS variable's value, trimmed, or the fallback when it is empty
    const cssVar = (name, fallback) => {
      const value = window.stylingUtils && window.stylingUtils.getCssVar ? String(window.stylingUtils.getCssVar(name, fallback) || '').trim() : '';
      return value || fallback;
    };

    // window.novaI18n key of each weather icon's word, for the hover tooltip
    const WEATHER_TIP_KEYS = {
        clear: 'weather_clear', partlyClear: 'weather_partly_clear',
        partlyCloudy: 'weather_partly_cloudy', cloudy: 'weather_cloudy',
        overcast: 'weather_overcast', seeingExc: 'seeing_excellent',
        seeingGood: 'seeing_good', seeingAvg: 'seeing_average',
        seeingPoor: 'seeing_poor', seeingBad: 'seeing_bad'
    };
    let weatherTip = null; // the hover tooltip <div>, created on first use

    const weatherOverlayPlugin = {
      id: 'weatherOverlay',
      beforeDatasetsDraw(chart) {
        chart.__weatherRects = []; // blocks the hover tooltip can hit, rebuilt each draw
        const info = chart.__weather;
        // --- NEW: Handle loading and error states ---
        if (!info) return; // Info object not even created yet
    
        if (info.isLoading) {
            this.drawMessage(chart, window.t('weather_loading'));
            return;
        }
        if (info.error) {
            this.drawMessage(chart, info.error);
            return;
        }
        // --- END NEW ---
    
        if (!info.hasWeather || !Array.isArray(info.forecast) || info.forecast.length === 0) return;
        const { forecast: originalForecast, cloudInfo, seeingInfo } = info;
    
        const hasValidSeeingData = originalForecast.some(b =>
            b.seeing != null && b.seeing !== -9999
        );
    
        // --- 3-hour grouping on local boundaries ---
        // Groups start at 00:00, 03:00, 06:00… in the plot timezone, as the iOS app's.
        // They are usually 3 hours long; across a DST change or a skipped local midnight
        // they can be 2, 2.5, 3.5, 4 or 5 hours. The boundaries come from a scan in
        // quarter hours, since every zone's offset is a multiple of 15 minutes; where the
        // clocks repeat an hour, the first of the two counts. Only groups that reach the
        // axis are built: the scan runs from one group before it to one after.
        const groupedForecast = [];
        const xScale = chart.scales && chart.scales.x;
        if (originalForecast.length > 0 && xScale) {
            originalForecast.sort((a, b) => a.start - b.start);

            // The edges depend only on the axis and the zone: scanned once per axis
            const edgesKey = xScale.min + '|' + xScale.max + '|' + plotTz;
            if (!chart.__weatherEdges || chart.__weatherEdges.key !== edgesKey) {
                const hourMs = 3600 * 1000;
                const quarterMs = 15 * 60 * 1000;
                const scanned = [];
                let lastStamp = null;
                for (let t = Math.floor((xScale.min - 5 * hourMs) / quarterMs) * quarterMs; t <= xScale.max + 5 * hourMs; t += quarterMs) {
                    const local = luxon.DateTime.fromMillis(t, { zone: plotTz });
                    if (local.minute !== 0 || local.hour % 3 !== 0) continue;
                    const stamp = local.toFormat('yyyy-MM-dd HH');
                    if (stamp === lastStamp) continue;
                    lastStamp = stamp;
                    scanned.push(t);
                }
                chart.__weatherEdges = { key: edgesKey, edges: scanned };
            }
            const edges = chart.__weatherEdges.edges;

            for (let i = 0; i + 1 < edges.length; i++) {
                const groupStart = edges[i];
                const groupEnd = edges[i + 1];
                if (groupEnd <= xScale.min || groupStart >= xScale.max) continue;
                const blocksInGroup = originalForecast.filter(b => b.start >= groupStart && b.start < groupEnd);
                if (blocksInGroup.length > 0) {
                    this.finalizeGroup(groupedForecast, blocksInGroup, groupStart, groupEnd);
                }
            }
        }
        // --- END GROUPING ---
    
        const { ctx, chartArea, scales } = chart;
        const x = scales.x;
        if (!x || !chartArea) return;
    
        const rowH = 18; // px per row
        const gap = 2;   // px between rows
        const pad = 1;   // inner padding for blocks
        const numRows = hasValidSeeingData ? 2 : 1;
        const totalH = rowH * numRows + (hasValidSeeingData ? gap : 0);
        const topY = chartArea.top - (totalH + 6);
        const iconPx = 14;         // icon size in CSS px
        const minIconBlockPx = 18; // narrower blocks show colour only

        // Icons replace the words when nova_weather_icons.js is loaded; without it the
        // band keeps its text labels.
        const icons = window.NovaWeatherIcons || null;
        const isDark = window.stylingUtils && window.stylingUtils.isDarkTheme && window.stylingUtils.isDarkTheme();
        // Light theme: --text-primary, white on Overcast only. Dark theme: --text-primary.
        const iconFor = (kind, level, block) => {
          const id = kind === 'cloud' ? icons.cloudIcon(level) : icons.seeingIcon(level);
          if (!id) return null;
          const colour = (!isDark && kind === 'cloud' && Number(level) === 9) ? cssVar('--text-white', '#fff') : cssVar('--text-primary', '#333');
          return { id, colour, kind, level, start: block.start, end: block.end };
        };
    
        ctx.save();
        ctx.globalAlpha = 1;
        ctx.shadowColor = 'transparent';
        ctx.font = '12px system-ui, Arial';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
    
        const getTextColorForBackground = (rgbaColor) => {
            if (!rgbaColor || !rgbaColor.startsWith('rgba')) return window.stylingUtils && window.stylingUtils.getColor ? window.stylingUtils.getColor('--text-primary', '#333') : '#333';
            try {
                const [r, g, b] = rgbaColor.match(/\d+/g).map(Number);
                const luminance = (0.299 * r + 0.587 * g + 0.114 * b);
                return luminance < 128 ? (window.stylingUtils && window.stylingUtils.getColor ? window.stylingUtils.getColor('--text-white', '#fff') : '#fff') : (window.stylingUtils && window.stylingUtils.getColor ? window.stylingUtils.getColor('--text-primary', '#333') : '#333');
            } catch (e) {
                return window.stylingUtils && window.stylingUtils.getColor ? window.stylingUtils.getColor('--text-primary', '#333') : '#333';
            }
        };
    
        const fitText = (text, maxPx) => {
          if (!text) return '';
          if (ctx.measureText(text).width <= maxPx) return text;
          const ell = '…';
          let lo = 0, hi = text.length;
          while (lo < hi) {
            const mid = Math.floor((lo + hi) / 2);
            const candidate = text.slice(0, mid) + ell;
            if (ctx.measureText(candidate).width <= maxPx) lo = mid + 1; else hi = mid;
          }
          return text.slice(0, Math.max(0, lo - 1)) + ell;
        };
    
        const drawBlock = (y, x0, x1, label, fill, icon) => {
          const left = Math.round(Math.min(x0, x1));
          const width = Math.max(1, Math.round(Math.abs(x1 - x0)));
          if (width < 1) return; // Don't draw tiny blocks
    
          ctx.fillStyle = fill;
          ctx.fillRect(left + pad, y + pad, width - 2 * pad, rowH - 2 * pad);
          ctx.strokeStyle = 'rgba(0,0,0,0.1)';
          ctx.lineWidth = 1;
          ctx.strokeRect(left + pad + 0.5, y + pad + 0.5, width - 2 * pad - 1, rowH - 2 * pad - 1);

          if (icon) {
            chart.__weatherRects.push({ x: left + pad, y: y + pad, w: width - 2 * pad, h: rowH - 2 * pad,
              kind: icon.kind, id: icon.id, level: icon.level, start: icon.start, end: icon.end });
          }

          if (icons) {
            // No icon (unknown level) or too narrow: colour only, no text
            if (!icon || width < minIconBlockPx) return;
            const img = this.iconImage(icons, icon.id, icon.colour, iconPx);
            if (img) {
              ctx.drawImage(img, Math.round(left + (width - iconPx) / 2), Math.round(y + (rowH - iconPx) / 2), iconPx, iconPx);
            }
            return;
          }
    
          const maxTextW = Math.max(0, width - 8);
          if (maxTextW < 5) return; // Don't draw text if too small
    
          const txt = fitText(label, maxTextW);
          ctx.fillStyle = getTextColorForBackground(fill);
          ctx.fillText(txt, left + width / 2, y + rowH / 2);
        };
    
        const xMinPx = x.getPixelForValue(x.min);
        const xMaxPx = x.getPixelForValue(x.max);
    
        let drawnBlocks = 0;
        groupedForecast.forEach(b => {
          let x0 = x.getPixelForValue(b.start);
          let x1 = x.getPixelForValue(b.end);
          if (!Number.isFinite(x0) || !Number.isFinite(x1)) return;
          x0 = Math.max(xMinPx, Math.min(x0, xMaxPx));
          x1 = Math.max(xMinPx, Math.min(x1, xMaxPx));
          if (Math.abs(x1 - x0) < 1) return;
          drawnBlocks++;
    
          const ci = cloudInfo[b.cloudcover];
          drawBlock(topY, x0, x1, ci ? ci.label : 'Clouds', ci ? weatherColour('cloud', b.cloudcover) : 'rgba(0,0,0,0.08)', icons && iconFor('cloud', b.cloudcover, b));
    
            if (hasValidSeeingData) {
                  if (b.seeing != null && b.seeing !== -9999) {
                      const si = seeingInfo[b.seeing];
                      drawBlock(topY + rowH + gap, x0, x1, si ? si.label : 'Seeing?', si ? weatherColour('seeing', b.seeing) : 'rgba(0,0,0,0.08)', icons && iconFor('seeing', b.seeing, b));
                  } else {
                       drawBlock(topY + rowH + gap, x0, x1, '', 'rgba(0,0,0,0.08)');
                  }
              }
        });
        ctx.restore();

        // Forecast data, but none of it on this night's axis
        if (drawnBlocks === 0) this.drawMessage(chart, window.t('weather_no_forecast'));
      },

      afterEvent(chart, args) {
        const e = args.event;
        if (e.type === 'mouseout') { this.hideTip(); return; }
        if (e.type !== 'mousemove') return;
        const hit = (chart.__weatherRects || []).find(r =>
          e.x >= r.x && e.x < r.x + r.w && e.y >= r.y && e.y < r.y + r.h);
        if (hit) this.showTip(chart, hit, e.x, e.y);
        else this.hideTip();
      },

      afterDestroy() { this.hideTip(); },
    
      // --- HELPER FUNCTIONS ---
      // Tooltip text: the block's hours in the plot timezone and its word, or the
      // seeing word in the seeing_label sentence.
      tipText(hit) {
          const word = window.t(WEATHER_TIP_KEYS[hit.id]);
          if (hit.kind === 'seeing') return window.t('seeing_label', { level: word });
          const hhmm = (ms) => luxon.DateTime.fromMillis(ms, { zone: plotTz }).toFormat('HH:mm');
          return hhmm(hit.start) + '–' + hhmm(hit.end) + ' · ' + word;
      },

      // One tooltip <div> beside the canvas, above the cursor (below it when there is
      // no room above), kept inside the chart container.
      showTip(chart, hit, x, y) {
          const canvas = chart.canvas;
          const box = canvas.parentNode;
          if (!box) return;
          if (!weatherTip) {
              weatherTip = document.createElement('div');
              weatherTip.className = 'chart-weather-tip';
              weatherTip.setAttribute('role', 'tooltip');
          }
          if (weatherTip.parentNode !== box) box.appendChild(weatherTip);
          weatherTip.textContent = this.tipText(hit);
          weatherTip.hidden = false;

          const offset = 12; // px between the cursor and the tooltip
          const px = canvas.offsetLeft + x;
          const py = canvas.offsetTop + y;
          const w = weatherTip.offsetWidth, h = weatherTip.offsetHeight;
          const left = Math.max(0, Math.min(px - w / 2, box.clientWidth - w));
          let top = py - h - offset;
          if (top < 0) top = Math.min(py + offset, box.clientHeight - h);
          weatherTip.style.left = Math.round(left) + 'px';
          weatherTip.style.top = Math.round(top) + 'px';
      },

      hideTip() {
          if (weatherTip) weatherTip.hidden = true;
      },

      // One icon of NovaWeatherIcons on its own canvas, px CSS px square at the screen's
      // devicePixelRatio. A cut shape erases with destination-out, which is why the icon
      // never draws straight onto the chart.
      iconImage(icons, id, colour, px) {
          const dpr = window.devicePixelRatio || 1;
          const key = id + '|' + colour + '|' + dpr;
          const cached = weatherIconCache.get(key);
          if (cached) return cached;

          const icon = icons.ICONS[id];
          if (!icon) return null;
          const vb = icon.viewBox.split(' ').map(Number);
          const size = Math.round(px * dpr);
          const canvas = document.createElement('canvas');
          canvas.width = canvas.height = size;
          const c = canvas.getContext('2d');
          c.scale(size / vb[2], size / vb[3]);
          c.translate(-vb[0], -vb[1]);
          c.fillStyle = c.strokeStyle = colour;
          for (const shape of icon.shapes) {
              const path = new Path2D(shape.d);
              c.save();
              c.lineWidth = shape.strokeWidth;
              if (shape.cut) {
                  c.globalCompositeOperation = 'destination-out';
                  c.fill(path);
                  c.stroke(path);
              } else {
                  c.lineJoin = shape.lineJoin;
                  c.globalAlpha = shape.opacity;
                  if (shape.fill !== 'none') c.fill(path);
                  if (shape.stroke !== 'none') c.stroke(path);
              }
              c.restore();
          }
          weatherIconCache.set(key, canvas);
          return canvas;
      },

      drawMessage(chart, text) {
          const { ctx, chartArea } = chart;
          if (!chartArea) return;
          const topY = chartArea.top - (18 * 2 + 2 + 6); // Same position as weather bars
          ctx.save();
          // --text-muted is too faint at this size on the dark theme
          const isDark = window.stylingUtils && window.stylingUtils.isDarkTheme && window.stylingUtils.isDarkTheme();
          ctx.fillStyle = cssVar(isDark ? '--text-secondary' : '--text-muted', 'rgba(0, 0, 0, 0.5)');
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.font = 'italic 12px system-ui, Arial';
          ctx.fillText(text, chartArea.left + chartArea.width / 2, topY + (18 + 2)); // Centered in the bar area
          ctx.restore();
      },
    
      finalizeGroup(groupedForecast, blocksInGroup, groupStart, groupEnd) {
          // Find the most frequent cloudcover value (mode) in the group
          const cloudCounts = blocksInGroup.reduce((acc, b) => {
              if (b.cloudcover != null) { // Only count valid cloud values
                  acc[b.cloudcover] = (acc[b.cloudcover] || 0) + 1;
              }
              return acc;
          }, {});
          // Default to 9 (Overcast) if no valid cloud data in group
          let dominantCloudcover = 9;
          const validClouds = Object.keys(cloudCounts);
          if (validClouds.length > 0) {
              dominantCloudcover = validClouds.reduce((a, b) => cloudCounts[a] > cloudCounts[b] ? a : b);
          }
    
          // Find dominant seeing value
          let dominantSeeing = -9999;
          const seeingCounts = blocksInGroup.reduce((acc, b) => {
              const s = b.seeing;
              if (s != null && s !== -9999) { // Only count valid seeing values
                  acc[s] = (acc[s] || 0) + 1;
              }
              return acc;
          }, {});
          const validSeeings = Object.keys(seeingCounts);
          if (validSeeings.length > 0) {
              dominantSeeing = validSeeings.reduce((a, b) => seeingCounts[a] > seeingCounts[b] ? a : b);
          }
    
          groupedForecast.push({
              start: groupStart,
              end: groupEnd,
              cloudcover: parseInt(dominantCloudcover),
              seeing: parseInt(dominantSeeing)
          });
      }
    };
    
    /**
     * Displays a formatted offline message inside a specified container.
     * @param {string} containerId The ID of the element to display the message in.
     * @param {string} message The text message to display.
     */
    function displayOfflineMessage(containerId, message) {
        const container = document.getElementById(containerId);
        if (!container) return;
        // Never wipe a live Aladin view: once the sky is up, an offline
        // condition is surfaced via showSurveyNotice(), not by replacing the DOM.
        if (aladin) return;
    
        // A simple, embedded SVG icon for "no connection"
        const offlineIconSvg = `
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <line x1="1" y1="1" x2="23" y2="23"></line>
                <path d="M16.72 11.06A10.94 10.94 0 0 1 19 12.55"></path>
                <path d="M5 12.55a10.94 10.94 0 0 1 5.17-2.39"></path>
                <path d="M10.71 5.05A16 16 0 0 1 22.58 9"></path>
                <path d="M1.42 9a15.91 15.91 0 0 1 4.7-2.88"></path>
                <path d="M8.53 16.11a6.5 6.5 0 0 1 6.95 0"></path>
                <line x1="12" y1="20" x2="12" y2="20"></line>
            </svg>`;
    
        container.innerHTML = `
            <div class="offline-message">
                ${offlineIconSvg}
                <p>${message}</p>
            </div>`;
    }
    
    const plotTz = NOVA_GRAPH_DATA.plotTz;
    Chart.defaults.adapters = Chart.defaults.adapters || {};
    Chart.defaults.adapters.date = {
        ...(Chart.defaults.adapters.date || {}),
        zone: plotTz
    };
    console.log('Adapter:', Chart._adapters?._date?.id, 'Zone:', Chart.defaults.adapters.date.zone);
    
    let aladin = null;
    let scanResultsCatalog = null;
    let scanFrameActive    = false;
    let scanResultsAll = [];
    let fovLayer = null;
    let altitudeChart = null;
    let displayedBaseSurvey = null;   // survey value of the base layer actually shown (may be the NSNS fallback)
    let displayedBlendSurvey = null;  // survey value of the blend layer actually shown, null when none

    // --- Secondary Object Comparison State ---
    let secondaryObjectName = null;           // Name of selected secondary object (slug)
    let secondaryObjectDisplayName = null;    // Display name for title (e.g., "Alves 2")
    let secondaryDatasetId = 'secondary_alt'; // Unique ID for the secondary dataset
    // --- End Secondary Object State ---

    // --- Survey layers: what Aladin actually shows ---------------------------
    // One rule for both layers: a survey is shown on the root the current mode
    // gives it (resolveHipsId) -- online the /hips/ proxy (disk first, only the
    // missing tiles fetched), in Offline view the cache-only /hips/offline/ root.
    // The server always answers a survey's properties, so the layer always
    // builds; a tile that cannot be had simply stays empty. There is no
    // survey-level "not available" decision and no notice about it.
    //
    // The displayed survey and the credit follow Aladin's confirmation (the
    // layer's successCallback). Aladin adds queued layers one after another, so
    // the last confirmation is the layer on screen.
    let baseWanted = null;   // survey of the latest base choice (shown or still loading)
    let latestBlend = null;  // the blend layer last asked for; null once taken off

    function createSurveyLayer(slot, survey, url, shownValue) {
        const options = {
            successCallback: () => onSurveyLayerShown(layer),
            // One survey failing is never a connectivity fact: this must not touch
            // Offline view (only the browser's offline/online events switch that).
            errorCallback: (err) => console.warn('[FRAMING] Survey layer failed to load:', err),
        };
        const name = surveyOptionLabel(survey) || url;
        const layer = aladin
            ? aladin.createImageSurvey(url, name, url, 'equatorial', 9, options)
            : A.HiPS(url, Object.assign({ name, cooFrame: 'equatorial', maxOrder: 9 }, options));
        layer.__nova = { slot, value: survey, survey: shownValue || survey, url };
        return layer;
    }

    // Aladin confirmed a layer: the state and the credit follow.
    // Called before Aladin puts the layer in its layer list (successCallback runs
    // first), so anything that must act on the list waits a tick.
    function onSurveyLayerShown(layer) {
        const info = layer && layer.__nova;
        if (!info) return;
        if (info.slot === 'base') {
            displayedBaseSurvey = info.survey;
            baseSurvey = layer;
            // On this layer itself, never via aladin.getBaseImageLayer(): Aladin
            // calls successCallback after the renderer holds the new layer but
            // before its own layer list does, so getBaseImageLayer() still returns
            // the old layer here -- and any setter on it writes the old layer's
            // metadata (its tile format too) over the new one's in the renderer.
            applyImageAdjustments(layer);
        } else if (layer !== latestBlend) {
            // A blend taken off (or replaced) while it was loading still landed:
            // take it off again rather than leave a layer nobody asked for.
            setTimeout(() => {
                if (aladin && aladin.getOverlayImageLayer && aladin.getOverlayImageLayer('blend') === layer) {
                    aladin.removeImageLayer('blend');
                }
            }, 0);
        } else {
            displayedBlendSurvey = info.survey;
            const op = Number(document.getElementById('blend-opacity')?.value || 0);
            if (typeof layer.setOpacity === 'function') layer.setOpacity(Math.max(0, Math.min(1, op)));
        }
        updateSurveyCredit();
    }

    // Take the blend layer off (a blend still loading is dropped when it lands).
    function removeBlendLayer() {
        latestBlend = null;
        if (aladin && aladin.getOverlayImageLayer && aladin.getOverlayImageLayer('blend')) {
            aladin.removeImageLayer('blend');
        }
        displayedBlendSurvey = null;
        updateSurveyCredit();
    }

    function ensureBlendLayer() {
        if (!aladin) return null;
        const sel = document.getElementById('blend-survey-select');
        if (!sel) return null;

        const surveyId = sel.value;
        const existing = aladin.getOverlayImageLayer && aladin.getOverlayImageLayer('blend');
        const blendOp = Number(document.getElementById('blend-opacity')?.value || 0);

        // CDS unreachable: DSS2 color comes from a mirror that answered; any other CDS blend
        // layer is skipped (saved blend values stay as they are)
        let resolvedId = resolveHipsId(surveyId);
        let shownValue = surveyId;   // what the credit sees: the select value, or the mirror URL
        if (!cdsReachable && window.framingUtils.isCdsUrl(resolvedId)) {
            if (surveyId === 'P/DSS2/color' && dssMirror) {
                resolvedId = dssMirror;
                shownValue = dssMirror;
            } else {
                removeBlendLayer();
                if (blendOp > 0) showSurveyNotice();
                return null;
            }
        }

        // Already asked for, for this survey and root (shown or still loading):
        // nothing to do. The "Blend with" change handler calls this twice
        // (directly and via setBlendOpacity); the second call finds the first.
        if (latestBlend && latestBlend.__nova.value === surveyId && latestBlend.__nova.url === resolvedId) {
            return latestBlend;
        }

        // Only build a blend layer that is visible
        if (!(blendOp > 0)) return existing || null;

        try {
            const layer = createSurveyLayer('blend', surveyId, resolvedId, shownValue);
            latestBlend = layer;
            aladin.setOverlayImageLayer(layer, 'blend');
            return layer;
        } catch (e) {
            console.warn('[nova] Could not create/set overlay image survey:', e);
            return null;
        }
    }
    function setBlendOpacity(a) { const v = Math.max(0, Math.min(1, Number(a) || 0)); if (!aladin) return; const layer = ensureBlendLayer(); if (layer && (typeof layer.setOpacity === 'function' || typeof layer.setAlpha === 'function')) { (layer.setOpacity || layer.setAlpha).call(layer, v); return; } try { const survey = aladin.getOverlayImageLayer && aladin.getOverlayImageLayer('blend'); survey?.setOpacity?.(v); } catch(e) {} }

    // --- Survey image credit (bottom right of the sky, left of the Aladin logo) ---
    const NSNS_URL_PREFIX = 'https://www.simg.de/nebulae3/';
    const NSNS_CREDIT = 'nsns'; // sentinel: rendered with links, see appendSurveyCredit()
    const DSS2_CREDIT = 'DSS2 (STScI) · HiPS: CDS';
    const SURVEY_CREDITS = {
        'P/DSS2/color': DSS2_CREDIT,
        'P/DSS2/red': DSS2_CREDIT,
        'P/allWISE/color': 'AllWISE (NASA/JPL-Caltech) · HiPS: CDS',
        'P/2MASS/color': '2MASS (UMass/IPAC-Caltech) · HiPS: CDS',
        'P/GALEXGR6/AIS/color': 'GALEX (NASA) · HiPS: CDS',
        'P/PanSTARRS/DR1/color-z-zg-g': 'Pan-STARRS1 DR1 · HiPS: CDS',
        'P/SDSS9/color': 'SDSS DR9 · HiPS: CDS'
    };
    const DSS_MIRROR_CREDIT = 'dssmirror:'; // sentinel prefix + mirror URL: DSS2 credit plus the mirror host
    function surveyCreditFor(value) {
        if (!value) return null;
        if (value.startsWith(NSNS_URL_PREFIX)) return NSNS_CREDIT;
        if (window.framingUtils.DSS_MIRRORS.includes(value)) return DSS_MIRROR_CREDIT + value;
        return Object.prototype.hasOwnProperty.call(SURVEY_CREDITS, value) ? SURVEY_CREDITS[value] : null;
    }
    function appendCreditLink(parent, text, href) {
        const a = document.createElement('a');
        a.href = href;
        a.target = '_blank';
        a.rel = 'noopener';
        a.textContent = text;
        parent.appendChild(a);
    }
    function appendSurveyCredit(parent, credit) {
        if (credit.startsWith(DSS_MIRROR_CREDIT)) {
            const mirrorUrl = credit.slice(DSS_MIRROR_CREDIT.length);
            parent.appendChild(document.createTextNode(DSS2_CREDIT + ' · '));
            appendCreditLink(parent, new URL(mirrorUrl).hostname, mirrorUrl);
            return;
        }
        if (credit !== NSNS_CREDIT) { parent.appendChild(document.createTextNode(credit)); return; }
        appendCreditLink(parent, 'Northern Sky Narrowband Survey', NSNS_URL_PREFIX);
        parent.appendChild(document.createTextNode(' © Stefan Ziegenbalg, '));
        appendCreditLink(parent, 'CC BY-NC-SA 4.0', 'https://creativecommons.org/licenses/by-nc-sa/4.0/');
    }
    function updateSurveyCredit() {
        const host = document.getElementById('aladin-lite-div');
        if (!host) return;
        let el = document.getElementById('survey-credit');
        if (!el) { el = document.createElement('div'); el.id = 'survey-credit'; host.appendChild(el); }
        // Credit the layers actually shown, not the selects (they differ in the CDS fallback)
        const mainValue = displayedBaseSurvey || '';
        const blendValue = displayedBlendSurvey || '';
        const blendOp = Number(document.getElementById('blend-opacity')?.value || 0);
        const credits = [];
        const mainCredit = surveyCreditFor(mainValue);
        if (mainCredit) credits.push(mainCredit);
        if (blendOp > 0 && blendValue !== mainValue) {
            const blendCredit = surveyCreditFor(blendValue);
            if (blendCredit && !credits.includes(blendCredit)) credits.push(blendCredit);
        }
        el.replaceChildren();
        credits.forEach((c, i) => { if (i) el.appendChild(document.createTextNode(' + ')); appendSurveyCredit(el, c); });
        el.hidden = credits.length === 0;
    }

    // --- Sky-survey notice (top of the sky, dismissible) ---
    // Only the store-off path uses it: CDS unreachable, a fallback survey shown
    // (chooseDisplayedBase). With the /hips/ proxy no survey ever gets a notice.
    let surveyNoticeDismissed = false;   // reset on every framing open
    let surveyNoticeText = null;
    function showSurveyNotice(message) {
        surveyNoticeText = message || window.t('survey_server_unreachable');
        renderSurveyNotice();
    }
    // The situation the notice described is over. A later one shows it again, so
    // this does not touch the user's own dismissal.
    function hideSurveyNotice() {
        surveyNoticeText = null;
        renderSurveyNotice();
    }
    function renderSurveyNotice() {
        const text = surveyNoticeText;
        let el = document.getElementById('survey-notice');
        if (!text || surveyNoticeDismissed) {
            if (el) el.hidden = true;
            return;
        }
        const host = document.getElementById('aladin-lite-div');
        if (!host) return;
        if (!el) {
            el = document.createElement('div');
            el.id = 'survey-notice';
            el.setAttribute('role', 'status');
            const textEl = document.createElement('span');
            textEl.className = 'survey-notice-text';
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'survey-notice-close';
            btn.setAttribute('aria-label', window.t('close'));
            btn.textContent = '×';
            btn.addEventListener('click', () => { surveyNoticeDismissed = true; el.hidden = true; });
            el.append(textEl, btn);
            host.appendChild(el);
        }
        el.querySelector('.survey-notice-text').textContent = text;
        el.hidden = false;
    }
    // --- Loading placeholder while the survey probes run and until Aladin has created its view ---
    function showSurveyLoading() {
        const host = document.getElementById('aladin-lite-div');
        if (!host || document.getElementById('survey-loading')) return;
        const el = document.createElement('div');
        el.id = 'survey-loading';
        const spinner = document.createElement('span');
        spinner.className = 'loading-spinner';
        const text = document.createElement('span');
        text.className = 'loading-message';
        text.textContent = window.t('loading');
        el.append(spinner, text);
        host.appendChild(el);
    }
    function hideSurveyLoading() {
        document.getElementById('survey-loading')?.remove();
    }
    function resetSurveyNotice() {
        surveyNoticeDismissed = false;
        hideSurveyNotice();
    }
    
    // Catalog layer for all Nova DB objects shown in Aladin
    let novaObjectsCatalog = null;
    let novaObjectsPromise = null;

    /**
     * Loads the Nova DB object list and adds it to Aladin as a catalog layer.
     * The rows come from /api/framing_objects - fetched once per page load, the
     * first time the Framing Assistant opens, instead of riding along in
     * NOVA_GRAPH_DATA.allObjects. A failed fetch means no markers and one
     * console.warn; the assistant itself keeps working, and the next open tries
     * again.
     */
    function ensureNovaObjectsCatalog() {
        if (!aladin) return;            // Need Aladin initialised first
        if (novaObjectsCatalog) return; // Already built in this session
        if (novaObjectsPromise) return novaObjectsPromise; // Already fetching

        novaObjectsPromise = fetch('/api/framing_objects')
            .then(resp => resp.ok ? resp.json() : Promise.reject(new Error('HTTP ' + resp.status)))
            .then(data => addNovaObjectsCatalog(Array.isArray(data.objects) ? data.objects : []))
            .catch(err => {
                console.warn('[FRAMING] Could not load the Nova objects catalog:', err);
                novaObjectsPromise = null;  // Let the next open try again
            });

        return novaObjectsPromise;
    }

    /**
     * Builds the "Nova objects" catalog layer from ensureNovaObjectsCatalog()'s
     * rows and adds it to Aladin. Unchanged from when the rows came inline: each
     * source is a small circle labelled with the object identifier.
     */
    function addNovaObjectsCatalog(objects) {
        if (!objects.length) return;

        // Create a catalog layer that Aladin will render as markers with labels
        const cat = A.catalog({
            name: 'Nova objects',
            shape: 'circle',          // use circular markers
            sourceSize: 10,
            color: window.stylingUtils && window.stylingUtils.getPrimaryColor ? window.stylingUtils.getPrimaryColor() : '#83b4c5',
            labelColumn: 'name',      // read label from the "name" field of each source
            displayLabel: true,       // draw the labels on the sky
            labelColor: window.stylingUtils && window.stylingUtils.getPrimaryColor ? window.stylingUtils.getPrimaryColor() : '#83b4c5',       // dark-ish blue labels
            labelFont: '16px sans-serif', // larger font for visibility
            labelHalo: true,          // white halo to separate text from background
            labelHaloColor: window.stylingUtils && window.stylingUtils.getCssVar('--text-white', '#fff') || '#fff',   // contrast halo for dark backgrounds
            onClick: (source) => {
                if (!source || !source.data) return;
                const objData = source.data;
    
                // 1. Resolve Image (Custom > DSS2 Fallback)
                let finalImg = objData.image_url;
                let finalSource = objData.image_credit || "Catalog";
                let finalLink = objData.image_source_link || "";
    
                // If no custom image, try calculating DSS2 URL using global helper
                if (!finalImg && typeof getAladinFallbackUrl === 'function') {
                    const raDeg = Number(objData.ra_deg) || 0;
                    const decDeg = Number(objData.dec_deg) || 0;
                    // 'Size' comes from nova.py enrichment
                    finalImg = getAladinFallbackUrl(raDeg, decDeg, objData.Size);
                    finalSource = "DSS2";
                }
    
                // 2. Resolve Description
                let finalText = objData.description_text;
                if (!finalText) {
                     const typeStr = objData.Type ? `Type: ${objData.Type}. ` : '';
                     const constStr = objData.Constellation ? `In ${objData.Constellation}.` : '';
                     finalText = `${typeStr}${constStr}`;
                }
    
                // 3. Open Modal via global hook
                if (typeof window.openInspirationModal === 'function') {
                    // Pass a normalized data object that matches what openInspirationModal/loadInspirationGraph expects
                    // IMPORTANT: The modal expects "Object" (capital O) for the title
                    const modalPayload = {
                        ...objData,
                        // Ensure keys match what _inspiration_section.html expects
                        Object: objData.Object || objData.object_name,
                        "Common Name": objData["Common Name"] || objData.common_name,
                        "Max Altitude (°)": "N/A", // Static view doesn't have live calcs
                        "Transit Time": "N/A",
                        "Observable Duration (min)": "0"
                    };
                    window.openInspirationModal(modalPayload, finalImg, finalText, finalSource, finalLink);
                }
            }
        });
    
        objects.forEach(obj => {
            if (!obj) return;
    
            const ra = Number(obj.ra_deg);
            const dec = Number(obj.dec_deg);
            if (!Number.isFinite(ra) || !Number.isFinite(dec)) return;
    
            // Prefer the Nova object identifier; fall back to common name or numeric id
            const label =
                (obj.object_name && String(obj.object_name).trim()) ||
                (obj.common_name && String(obj.common_name).trim()) ||
                String(obj.id);
    
            // Add leading non-breaking spaces to visually offset label from circle
            const labelWithPadding = '\u00A0\u00A0' + label;  // 3 spaces worth of padding
    
            // Merge the object data with the label ('name') into a single flat object
            // This ensures Aladin finds 'name' for the label AND 'obj' properties for the click handler
            const sourceData = Object.assign({}, obj, { name: labelWithPadding });
    
            const src = A.source(ra, dec, sourceData);
    
            cat.addSources([src]);
        });
    
        aladin.addCatalog(cat);
        novaObjectsCatalog = cat;
    }
    
    function getDateTimeMs(baseDateISO, timeStr) {
        if (!timeStr || !timeStr.includes(':')) return null;
        const [hour, minute] = timeStr.split(':').map(Number);
        const base = luxon.DateTime.fromISO(baseDateISO, {zone: plotTz}).startOf('day');
        return base.set({hour, minute, second: 0, millisecond: 0}).toMillis();
    }
    
    async function fetchAndUpdateWeather(chartInstance, lat, lon, tz, isOffline) {
        if (isOffline || !navigator.onLine) {
            console.log("Weather fetch skipped (offline).");
            if (chartInstance) {
                chartInstance.__weather.isLoading = false;
                chartInstance.__weather.error = window.t('weather_unavailable_offline');
                chartInstance.update('none');
            }
            return;
        }
        if (!chartInstance) {
            console.log("Weather fetch skipped (no chart).");
            return;
        }
    

        try {
            const apiUrl = `/api/get_weather_forecast?lat=${lat}&lon=${lon}&tz=${encodeURIComponent(tz)}`;
            const resp = await fetch(apiUrl);
            if (!resp.ok) throw new Error(`Weather API failed: ${resp.status}`);
    
            const data = await resp.json();
    
            const toMs = (val) => {
                if (typeof val === 'number') return (val < 1e12 ? val * 1000 : val);
                if (typeof val === 'string') {
                    const hasOffset = /[Zz]|[+\-]\d{2}:?\d{2}$/.test(val);
                    // Weather API returns UTC ISO strings, parse as such, convert to local tz, get millis
                    if (hasOffset) return luxon.DateTime.fromISO(val, { zone: 'utc' }).toMillis();
                    else return luxon.DateTime.fromISO(val, { zone: plotTz }).toMillis();
                }
                return null;
            }
    
            const hasWeather = Array.isArray(data.weather_forecast) && data.weather_forecast.length > 0;
            const forecastForOverlay = hasWeather ? data.weather_forecast.map(b => ({
                start: toMs(b.start),
                end: toMs(b.end),
                cloudcover: b.cloudcover,
                seeing: b.seeing
            })) : [];
    
            const drawableForecast = forecastForOverlay.filter(b => Number.isFinite(b.start) && Number.isFinite(b.end) && b.end > b.start);
            const hasDrawableWeather = drawableForecast.length > 0;
    
            // Get the original cloud/seeing info maps from the chart object
            const { cloudInfo, seeingInfo } = chartInstance.__weather || {};
    
            // Update the chart's internal weather data
            chartInstance.__weather = {
                hasWeather: hasDrawableWeather,
                forecast: drawableForecast,
                cloudInfo: cloudInfo, // Persist original maps
                seeingInfo: seeingInfo, // Persist original maps
                isLoading: false,
                error: hasDrawableWeather ? null : window.t('weather_unavailable')
            };
    
            // Redraw the chart to show the weather overlay
            chartInstance.update('none'); // Use 'none' to avoid animation
        } catch (err) {
            console.error('Could not fetch or apply weather data:', err);
            chartInstance.__weather.isLoading = false;
            chartInstance.__weather.error = window.t('weather_unavailable');
            chartInstance.update('none');
        }
    }

    // ========================================================================
    // Private Helper Functions for renderClientSideChart()
    // ========================================================================

    /**
     * Convert time value to milliseconds for chart
     * @param {number|string} val - Time value (can be unix timestamp or ISO string)
     * @param {string} plotTz - Timezone for the plot
     * @returns {number|null} - Time in milliseconds
     */
    function _convertToMilliseconds(val, plotTz) {
        if (typeof val === 'number') return (val < 1e12 ? val * 1000 : val);
        if (typeof val === 'string') {
            const hasOffset = /[Zz]|[+\-]\d{2}:?\d{2}$/.test(val);
            if (hasOffset) return luxon.DateTime.fromISO(val).setZone(plotTz).toMillis();
            else return luxon.DateTime.fromISO(val, {zone: plotTz}).toMillis();
        }
        return null;
    }

    /**
     * Build the "Now" line annotation for current time
     * @param {number} nowMs - Current time in milliseconds
     * @returns {Object} - Chart.js annotation configuration
     */
    function _buildCurrentTimeAnnotation(nowMs) {
        return {
            type: 'line',
            borderColor: 'rgba(255, 99, 132, 0.8)',
            borderWidth: 3,
            borderDash: [10, 3],
            label: {
                display: true,
                content: 'Now',
                position: 'start',
                rotation: 90,
                font: { size: 10, weight: 'bold' },
                color: 'rgba(255, 99, 132, 1)',
                backgroundColor: 'rgba(255, 255, 255, 0.7)'
            },
            xMin: nowMs,
            xMax: nowMs,
            xScaleID: 'x'
        };
    }

    /**
     * Build sun event annotations (sunset, sunrise, dusk, dawn)
     * @param {Object} sunEvents - Sun events data {current, next}
     * @param {Object} baseDt - Luxon DateTime for base date
     * @param {Object} nextDt - Luxon DateTime for next day
     * @returns {Object} - Map of annotation configurations
     */
    function _buildSunEventAnnotations(sunEvents, baseDt, nextDt) {
        const annotations = {};

        function wallTimeMs(baseDateTime, timeStr) {
            if (!timeStr || !timeStr.includes(':')) return null;
            const [h, m] = timeStr.split(':').map(Number);
            return baseDateTime.set({hour: h, minute: m, second: 0, millisecond: 0}).toMillis();
        }

        const sunsetTimeCurrent = wallTimeMs(baseDt, sunEvents.current.sunset);
        let duskTime;
        const duskTimeCurrent = wallTimeMs(baseDt, sunEvents.current.astronomical_dusk);
        if (duskTimeCurrent && sunsetTimeCurrent && duskTimeCurrent < sunsetTimeCurrent) {
            duskTime = wallTimeMs(nextDt, sunEvents.current.astronomical_dusk);
        } else {
            duskTime = duskTimeCurrent;
        }
        const dawnTime = wallTimeMs(nextDt, sunEvents.next.astronomical_dawn);
        const sunriseTime = wallTimeMs(nextDt, sunEvents.next.sunrise);
        const sunsetTime = sunsetTimeCurrent;

        const labelConfig = {
            display: true,
            position: 'start',
            rotation: 90,
            font: {size: 10, weight: '400'},
            color: (window.stylingUtils && window.stylingUtils.isDarkTheme && window.stylingUtils.isDarkTheme()) ? '#e0e0e0' : '#222',
            backgroundColor: (window.stylingUtils && window.stylingUtils.isDarkTheme && window.stylingUtils.isDarkTheme()) ? 'rgba(40,40,40,0.92)' : 'rgba(255,255,255,0.92)',
            borderColor: (window.stylingUtils && window.stylingUtils.isDarkTheme && window.stylingUtils.isDarkTheme()) ? 'rgba(100,100,100,0.5)' : 'rgba(0,0,0,0.15)',
            borderWidth: 1
        };

        if (sunsetTime) {
            annotations.sunsetLine = {
                type: 'line',
                xMin: sunsetTime,
                xMax: sunsetTime,
                borderColor: 'black',
                borderWidth: 1,
                label: { ...labelConfig, content: 'Sunset' }
            };
        }
        if (duskTime) {
            annotations.duskLine = {
                type: 'line',
                xMin: duskTime,
                xMax: duskTime,
                borderColor: 'black',
                borderWidth: 1,
                label: { ...labelConfig, content: 'Astronomical dusk' }
            };
        }
        if (dawnTime) {
            annotations.dawnLine = {
                type: 'line',
                xMin: dawnTime,
                xMax: dawnTime,
                borderColor: 'black',
                borderWidth: 1,
                label: { ...labelConfig, content: 'Astronomical dawn' }
            };
        }
        if (sunriseTime) {
            annotations.sunriseLine = {
                type: 'line',
                xMin: sunriseTime,
                xMax: sunriseTime,
                borderColor: 'black',
                borderWidth: 1,
                label: { ...labelConfig, content: 'Sunrise' }
            };
        }

        return { annotations, duskTime, dawnTime };
    }

    /**
     * Build transit time annotations
     * @param {string} transitTime - Transit time string (may include multiple times)
     * @param {Object} baseDt - Luxon DateTime for base date
     * @param {number} duskTime - Dusk time in milliseconds
     * @param {number} dawnTime - Dawn time in milliseconds
     * @returns {Object} - Map of transit annotation configurations
     */
    function _buildTransitAnnotations(transitTime, baseDt, duskTime, dawnTime) {
        const annotations = {};
        if (!transitTime || transitTime === "N/A") return annotations;

        const transitStrings = transitTime.split(',').map(s => s.trim());

        transitStrings.forEach((tStr, idx) => {
            const parts = tStr.split(':');
            const th = Number(parts[0] || 0);
            const tm = Number(parts[1] || 0);
            const t0 = baseDt.set({hour: th, minute: tm, second: 0, millisecond: 0}).toMillis();
            const t1 = baseDt.plus({days: 1}).set({hour: th, minute: tm, second: 0, millisecond: 0}).toMillis();

            const nightStart = duskTime;
            const nightEnd = dawnTime;
            const inWindow = (t) => t >= nightStart && t <= nightEnd;

            const transitMs = inWindow(t0) ? t0 : t1;

            // Only draw if within dark window
            if (transitMs < nightStart || transitMs > nightEnd) {
                return;
            }

            const annotKey = `transitLine_${idx}`;
            annotations[annotKey] = {
                type: 'line',
                xMin: transitMs,
                xMax: transitMs,
                borderColor: 'crimson',
                borderWidth: 2,
                borderDash: [6, 6],
                clip: false,
                label: {
                    display: true,
                    content: tStr,
                    position: 'start',
                    rotation: 90,
                    font: {size: 10, weight: 'bold'},
                    color: 'crimson',
                    backgroundColor: 'rgba(255,255,255,0.7)'
                }
            };
        });

        return annotations;
    }

    /**
     * Create the night shade plugin for Chart.js
     * @param {number} duskTime - Dusk time in milliseconds
     * @param {number} dawnTime - Dawn time in milliseconds
     * @returns {Object} - Chart.js plugin configuration
     */
    function _createNightShadePlugin(duskTime, dawnTime) {
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
                // Use darker shade in dark mode
                const isDark = window.stylingUtils && window.stylingUtils.isDarkTheme && window.stylingUtils.isDarkTheme();
                ctx.fillStyle = isDark ? 'rgba(50, 50, 50, 0.6)' : 'rgba(211, 211, 211, 0.7)';
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
     * Build Chart.js datasets for altitude and azimuth
     * @param {Object} data - Chart data from API
     * @param {Array} labels - Time labels in milliseconds
     * @param {string} objectName - Name of the astronomical object
     * @returns {Array} - Chart.js dataset configurations
     */
    function _buildChartDatasets(data, labels, objectName) {
        return [
            { label: `${objectName} Altitude`, data: data.object_alt, borderColor: (window.stylingUtils && window.stylingUtils.getChartLineColor) ? window.stylingUtils.getChartLineColor(0) : '#36A2EB', yAxisID: 'yAltitude', borderWidth: 4, pointRadius: 0, tension: 0.1 },
            { label: 'Moon Altitude', data: data.moon_alt, borderColor: (window.stylingUtils && window.stylingUtils.getChartLineColor) ? window.stylingUtils.getChartLineColor(1) : '#FFC107', yAxisID: 'yAltitude', borderWidth: 4, pointRadius: 0, tension: 0.1 },
            { label: 'Horizon Mask', data: data.horizon_mask_alt, borderColor: (window.stylingUtils && window.stylingUtils.getChartLineColor) ? window.stylingUtils.getChartLineColor(2) : '#636e72', backgroundColor: (window.stylingUtils && window.stylingUtils.getCssVarAsRgba) ? window.stylingUtils.getCssVarAsRgba('--chart-line-3', 'rgba(99, 110, 114, 0.3)', 0.3) : 'rgba(99, 110, 114, 0.3)', yAxisID: 'yAltitude', borderWidth: 2, pointRadius: 0, tension: 0.1, fill: 'start' },
            { label: 'Horizon', data: Array(labels.length).fill(0), borderColor: (window.stylingUtils && window.stylingUtils.getCssVar) ? window.stylingUtils.getCssVar('--text-primary', 'black') : 'black', yAxisID: 'yAltitude', borderWidth: 2, pointRadius: 0 },
            { label: `${objectName} Azimuth`, data: data.object_az, borderColor: (window.stylingUtils && window.stylingUtils.getChartLineColor) ? window.stylingUtils.getChartLineColor(0) : '#36A2EB', yAxisID: 'yAzimuth', borderDash: [5, 5], borderWidth: 3.5, pointRadius: 0, tension: 0.1 },
            { label: 'Moon Azimuth', data: data.moon_az, borderColor: (window.stylingUtils && window.stylingUtils.getChartLineColor) ? window.stylingUtils.getChartLineColor(1) : '#FFC107', yAxisID: 'yAzimuth', borderDash: [5, 5], borderWidth: 3.5, pointRadius: 0, tension: 0.1 },

            // 7. Skyglow Horizon (filled area, yAltitude axis — only if data available)
            ...(data.skyglow_alt ? [{
                label: 'Skyglow Floor',
                data: data.skyglow_alt,
                borderColor: 'rgba(255, 160, 50, 0.6)',
                backgroundColor: 'rgba(255, 140, 0, 0.15)',
                yAxisID: 'yAltitude',
                borderWidth: 1.5,
                pointRadius: 0,
                tension: 0.3,
                fill: {target: 3},
                borderDash: [3, 3]
            }] : [])
        ];
    }

    /**
     * Build Chart.js options configuration
     * @param {Object} annotations - Annotation configurations
     * @param {string} plotTz - Timezone for the plot
     * @param {string} plotLocName - Location name for display
     * @param {number} xMinCentered - Minimum x-axis value
     * @param {number} xMaxCentered - Maximum x-axis value
     * @param {string} objectName - Name of the astronomical object
     * @param {string} date - Date string for display
     * @returns {Object} - Chart.js options configuration
     */
    function _buildChartOptions(annotations, plotTz, plotLocName, xMinCentered, xMaxCentered, objectName, date) {
        return {
            responsive: true,
            maintainAspectRatio: true,
            aspectRatio: 2,
            adapters: {date: {zone: plotTz}},
            layout: { padding: { top: 46 } },
            plugins: {
                annotation: {annotations},
                legend: {position: 'right'},
                title: {
                    display: false,
                    text: `Altitude and Azimuth for ${objectName} on ${date}`,
                    align: 'start',
                    font: {size: 16}
                }
            },
            scales: {
                x: {
                    type: 'time',
                    adapters: { date: { zone: plotTz }},
                    parsing: false,
                    time: {unit: 'hour', displayFormats: {hour: 'HH:mm'}},
                    min: xMinCentered,
                    max: xMaxCentered,
                    bounds: 'ticks',
                    ticks: {source: 'auto'},
                    grid: {color: 'rgba(128,128,128,0.5)', borderDash: [2, 2]},
                    title: {display: true, text: `Time (Local - ${plotLocName})`}
                },
                yAltitude: {
                    position: 'left',
                    min: -90,
                    max: 90,
                    title: {display: true, text: 'Altitude (°)'},
                    grid: {color: 'rgba(128,128,128,0.5)', borderDash: [2, 2]}
                },
                yAzimuth: {
                    position: 'right',
                    min: 0,
                    max: 360,
                    title: {display: true, text: 'Azimuth (°)'},
                    grid: {drawOnChartArea: false}
                }
            }
        };
    }

    // ==========================================================================
    // SECONDARY OBJECT COMPARISON FUNCTIONS
    // ==========================================================================

    /**
     * Fetches observable active objects for the secondary selector dropdown.
     * @param {string} primaryName - The primary object name to exclude
     */
    async function fetchAndPopulateSecondarySelector(primaryName) {
        const select = document.getElementById('secondary-object-select');
        if (!select) return;

        // Get current location from NOVA_GRAPH_DATA (dashboard state)
        const plotLat = NOVA_GRAPH_DATA.plotLat;
        const plotLon = NOVA_GRAPH_DATA.plotLon;
        const plotTz = NOVA_GRAPH_DATA.plotTz;

        // Get currently selected graph date from the date controls
        const selectedDay = document.getElementById('day-select')?.value;
        const selectedMonth = document.getElementById('month-select')?.value;
        const selectedYear = document.getElementById('year-select')?.value;

        try {
            // Build API URL with location parameters (include location name and date for proper calculation)
            let apiUrl = `/api/get_observable_objects?exclude=${encodeURIComponent(primaryName)}`;
            if (plotLat !== undefined && plotLat !== null && plotLat !== '') {
                apiUrl += `&lat=${encodeURIComponent(plotLat)}`;
            }
            if (plotLon !== undefined && plotLon !== null && plotLon !== '') {
                apiUrl += `&lon=${encodeURIComponent(plotLon)}`;
            }
            if (plotTz) {
                apiUrl += `&tz=${encodeURIComponent(plotTz)}`;
            }
            // Pass location name for proper horizon mask lookup from database
            if (NOVA_GRAPH_DATA.plotLocName) {
                apiUrl += `&location=${encodeURIComponent(NOVA_GRAPH_DATA.plotLocName)}`;
            }
            // Pass the selected graph date so calculations match the displayed chart
            if (selectedDay !== undefined && selectedDay !== null && selectedDay !== '') {
                apiUrl += `&day=${encodeURIComponent(selectedDay)}`;
            }
            if (selectedMonth !== undefined && selectedMonth !== null && selectedMonth !== '') {
                apiUrl += `&month=${encodeURIComponent(selectedMonth)}`;
            }
            if (selectedYear !== undefined && selectedYear !== null && selectedYear !== '') {
                apiUrl += `&year=${encodeURIComponent(selectedYear)}`;
            }

            const resp = await fetch(apiUrl);
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

            const data = await resp.json();
            const objects = data.objects || [];

            // Clear and populate dropdown
            select.innerHTML = '<option value="">None</option>';

            objects.forEach(obj => {
                const option = document.createElement('option');
                option.value = obj.object_name;
                // Store common_name in data attribute for title updates
                option.dataset.commonName = obj.common_name || obj.object_name;
                // Display format: "Name (XXX min, max YY°)"
                const displayDuration = obj.observable_minutes >= 60
                    ? `${Math.floor(obj.observable_minutes / 60)}h ${obj.observable_minutes % 60}m`
                    : `${obj.observable_minutes}m`;
                option.textContent = `${obj.common_name || obj.object_name} (${displayDuration}, max ${obj.max_altitude}°)`;
                select.appendChild(option);
            });

            // Restore previous selection if valid
            if (secondaryObjectName && objects.some(o => o.object_name === secondaryObjectName)) {
                select.value = secondaryObjectName;
            } else {
                // Clear stale selection state
                secondaryObjectName = null;
                secondaryObjectDisplayName = null;
            }

            select.disabled = false;

            if (objects.length === 0) {
                const option = document.createElement('option');
                option.value = '';
                option.textContent = 'No observable active objects';
                option.disabled = true;
                select.appendChild(option);
            }
        } catch (err) {
            console.error('[Secondary Selector] Error fetching objects:', err);
            select.innerHTML = '<option value="">Error loading objects</option>';
            select.disabled = false;
        }
    }

    /**
     * Updates the visible page title (h2 element) based on primary and secondary object selection.
     * Format: Name (ID) & Name (ID)
     * @param {string} primaryName - The primary object's display name
     * @param {string} primaryId - The primary object's ID/slug
     * @param {string|null} secondaryName - The secondary object's display name (or null)
     * @param {string|null} secondaryId - The secondary object's ID/slug (or null)
     */
    function updateVisiblePageTitle(primaryName, primaryId, secondaryName = null, secondaryId = null) {
        const titleEl = document.getElementById('graph-title');
        if (!titleEl) return;

        // Clean slate: completely rebuild the title from scratch
        // Strict logic: Name always comes before ID
        // Single: Name <span class='subtitle'>(ID)</span>
        // Dual: Name1 <span class='subtitle'>(ID1)</span> & Name2 <span class='subtitle'>(ID2)</span>
        // Note: The .subtitle class has margin-left: 8px, so we don't add a space in the text node

        if (secondaryName && secondaryId) {
            // Dual mode: rebuild both objects
            titleEl.replaceChildren(
                document.createTextNode(primaryName),
                createSubtitleSpan(` (${primaryId})`),
                document.createTextNode(' & '),
                document.createTextNode(secondaryName),
                createSubtitleSpan(` (${secondaryId})`)
            );
        } else {
            // Single mode: rebuild only primary object
            titleEl.replaceChildren(
                document.createTextNode(primaryName),
                createSubtitleSpan(` (${primaryId})`)
            );
        }

        // Helper to create a subtitle span element
        function createSubtitleSpan(content) {
            const span = document.createElement('span');
            span.className = 'subtitle';
            span.textContent = content;
            return span;
        }
    }

    /**
     * Updates the page title based on primary and secondary object selection.
     * @param {string} primaryName - The primary object's display name
     * @param {string} primaryId - The primary object's ID/slug
     * @param {string|null} secondaryName - The secondary object's display name (or null)
     * @param {string|null} secondaryId - The secondary object's ID/slug (or null)
     */
    function updatePageTitle(primaryName, primaryId, secondaryName = null, secondaryId = null) {
        // Update visible page title
        updateVisiblePageTitle(primaryName, primaryId, secondaryName, secondaryId);

        // Update browser tab title
        const baseTitle = secondaryName && secondaryId
            ? `${primaryName} (${primaryId}) & ${secondaryName} (${secondaryId}) – Nova DSO Tracker`
            : `${primaryName} (${primaryId}) – Nova DSO Tracker`;
        document.title = baseTitle;
    }

    /**
     * Removes the secondary dataset from the chart if it exists.
     * Uses robust filtering by dataset ID to ensure only the secondary is removed.
     */
    function removeSecondaryDataset() {
        if (!window.altitudeChart) return;

        // Filter out the secondary dataset by its unique ID
        const originalLength = window.altitudeChart.data.datasets.length;
        window.altitudeChart.data.datasets = window.altitudeChart.data.datasets.filter(
            ds => ds.id !== secondaryDatasetId
        );

        if (window.altitudeChart.data.datasets.length !== originalLength) {
            console.log('[Secondary Object] Removed secondary dataset from chart');
            window.altitudeChart.update('none');
        }
    }

    /**
     * Truncates a string to maxLen characters, adding "..." if truncated.
     * @param {string} str - String to truncate
     * @param {number} maxLen - Maximum length (default 20)
     * @returns {string} - Truncated string
     */
    function truncateLabel(str, maxLen = 20) {
        if (!str || str.length <= maxLen) return str;
        return str.substring(0, maxLen).trim() + '...';
    }

    /**
     * Adds the secondary object's altitude data to the chart as a magenta line.
     * @param {Object} plotData - The plot data from the API (contains object_alt)
     * @param {string} objectId - The secondary object's ID/slug (for compact legend)
     * @param {string} displayName - The secondary object's display name (for tooltips)
     */
    function addSecondaryDataset(plotData, objectId, displayName) {
        if (!window.altitudeChart || !plotData) return;

        // First remove any existing secondary dataset
        removeSecondaryDataset();

        // Use object ID for legend (compact), full name for tooltip
        const legendLabel = truncateLabel(objectId, 22);

        // Create the secondary altitude dataset with unique ID
        const secondaryDataset = {
            id: secondaryDatasetId,
            label: legendLabel,
            fullLabel: displayName || objectId, // Store full display name for tooltips
            data: plotData.object_alt,
            borderColor: (window.stylingUtils && window.stylingUtils.getChartLineColor) ? window.stylingUtils.getChartLineColor(3) : '#FF00FF', // Magenta (chart line 4)
            yAxisID: 'yAltitude',
            borderWidth: 3,
            pointRadius: 0,
            tension: 0.1,
            order: 999 // Render on top of other lines
        };

        window.altitudeChart.data.datasets.push(secondaryDataset);

        // Update tooltip callback to use fullLabel when available
        if (!window.altitudeChart.options.plugins.tooltip.callbacks) {
            window.altitudeChart.options.plugins.tooltip.callbacks = {};
        }
        window.altitudeChart.options.plugins.tooltip.callbacks.label = function(context) {
            // Use fullLabel if available (for secondary object), otherwise use regular label
            const label = context.dataset.fullLabel || context.dataset.label || '';
            const value = context.parsed.y !== null ? context.parsed.y.toFixed(1) + '°' : 'N/A';
            return label + ': ' + value;
        };

        window.altitudeChart.update('none');
        console.log(`[Secondary Object] Added ${displayName} to chart (legend: "${legendLabel}")`);
    }

    /**
     * Fetches plot data for the secondary object and adds it to the chart.
     * @param {string} objectId - The secondary object ID/slug
     * @param {string|null} displayName - The display name for the title (or null to clear)
     */
    async function loadSecondaryObject(objectId, displayName = null) {
        if (!objectId) {
            // Clear secondary
            secondaryObjectName = null;
            secondaryObjectDisplayName = null;
            removeSecondaryDataset();
            // Restore primary-only title
            const primaryName = NOVA_GRAPH_DATA.altName || NOVA_GRAPH_DATA.objectName;
            const primaryId = NOVA_GRAPH_DATA.objectName;
            updatePageTitle(primaryName, primaryId);
            return;
        }

        const day = document.getElementById('day-select').value;
        const month = document.getElementById('month-select').value;
        const year = document.getElementById('year-select').value;
        const plotLat = NOVA_GRAPH_DATA.plotLat;
        const plotLon = NOVA_GRAPH_DATA.plotLon;
        const plotTz = NOVA_GRAPH_DATA.plotTz;
        const plotLocName = NOVA_GRAPH_DATA.plotLocName;

        try {
            const apiUrl = `/api/get_plot_data/${encodeURIComponent(objectId)}?day=${day}&month=${month}&year=${year}&plot_lat=${plotLat}&plot_lon=${plotLon}&plot_tz=${encodeURIComponent(plotTz)}&plot_loc_name=${encodeURIComponent(plotLocName)}`;
            console.log(`[Secondary Object] Fetching plot data for ${objectId}`);

            const resp = await fetch(apiUrl);
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

            const plotData = await resp.json();

            // Store state
            secondaryObjectName = objectId;
            secondaryObjectDisplayName = displayName || objectId;

            // Add to chart - pass objectId for legend, displayName for tooltip
            addSecondaryDataset(plotData, objectId, secondaryObjectDisplayName);

            // Update title: "PrimaryName (PrimaryID) & SecondaryName (SecondaryID)"
            const primaryName = NOVA_GRAPH_DATA.altName || NOVA_GRAPH_DATA.objectName;
            const primaryId = NOVA_GRAPH_DATA.objectName;
            updatePageTitle(primaryName, primaryId, secondaryObjectDisplayName, objectId);
            console.log(`[Secondary Object] Title updated to: ${document.title}`);
        } catch (err) {
            console.error(`[Secondary Object] Error loading ${objectId}:`, err);
            // Reset selection on error
            const select = document.getElementById('secondary-object-select');
            if (select) select.value = '';
            secondaryObjectName = null;
            secondaryObjectDisplayName = null;
        }
    }

    /**
     * Initialize the secondary object selector and event handlers.
     * Called once when the page loads.
     */
    function initSecondaryObjectSelector() {
        const select = document.getElementById('secondary-object-select');
        if (!select) return;

        // Fetch and populate the dropdown
        fetchAndPopulateSecondarySelector(NOVA_GRAPH_DATA.objectName);

        // Add event listeners for date controls to refresh dropdown when date changes
        ['day-select', 'month-select', 'year-select'].forEach(function(id) {
            const el = document.getElementById(id);
            if (el) {
                el.addEventListener('change', () => {
                    console.log(`[Secondary Selector] Date control changed: ${id}, refreshing dropdown`);
                    if (typeof fetchAndPopulateSecondarySelector === 'function') {
                        fetchAndPopulateSecondarySelector(NOVA_GRAPH_DATA.objectName);
                    }
                });
            }
        });

        // Handle selection changes
        select.addEventListener('change', (e) => {
            const selectedOption = e.target.options[e.target.selectedIndex];
            const selectedValue = e.target.value;

            if (selectedValue) {
                // Get display name from data attribute using getAttribute (more reliable)
                let displayName = selectedOption?.getAttribute('data-common-name');
                console.log(`[Secondary Selector] data-common-name: "${displayName}"`);

                // Fallback: extract from text content if attribute missing
                // Text format: "Name (Xh Ym, max Z°)" - extract just the name part
                if (!displayName && selectedOption?.textContent) {
                    const textMatch = selectedOption.textContent.match(/^(.+?)\s*\(\d/);
                    displayName = textMatch ? textMatch[1].trim() : selectedValue;
                    console.log(`[Secondary Selector] Extracted from text: "${displayName}"`);
                }

                // Final fallback to value
                displayName = displayName || selectedValue;

                // Store state
                secondaryObjectName = selectedValue;
                secondaryObjectDisplayName = displayName;

                // Build title: "PrimaryName (PrimaryID) & SecondaryName (SecondaryID)"
                const primaryName = NOVA_GRAPH_DATA.altName || NOVA_GRAPH_DATA.objectName;
                const primaryId = NOVA_GRAPH_DATA.objectName;
                updatePageTitle(primaryName, primaryId, displayName, selectedValue);
                console.log(`[Secondary Selector] Title set to: "${document.title}"`);

                // Load the chart data
                loadSecondaryObject(selectedValue, displayName);
            } else {
                // Clear secondary immediately
                secondaryObjectName = null;
                secondaryObjectDisplayName = null;
                removeSecondaryDataset();
                // Restore primary-only title
                const primaryName = NOVA_GRAPH_DATA.altName || NOVA_GRAPH_DATA.objectName;
                const primaryId = NOVA_GRAPH_DATA.objectName;
                updatePageTitle(primaryName, primaryId);
                console.log(`[Secondary Selector] Title cleared to primary only`);
            }
        });

        // Re-fetch when date changes (to update observable durations)
        ['day-select', 'month-select', 'year-select'].forEach(id => {
            const el = document.getElementById(id);
            if (el) {
                el.addEventListener('change', () => {
                    // Re-populate dropdown after a short delay
                    setTimeout(() => {
                        fetchAndPopulateSecondarySelector(NOVA_GRAPH_DATA.objectName);
                    }, 100);
                    // If we had a secondary selected, reload it for the new date
                    if (secondaryObjectName && secondaryObjectDisplayName) {
                        loadSecondaryObject(secondaryObjectName, secondaryObjectDisplayName);
                    }
                });
            }
        });
    }

    // --- END SECONDARY OBJECT COMPARISON FUNCTIONS ---


    // ==========================================================================
    // CLIENT-SIDE PLOT DATA (day, month and year views)
    // ==========================================================================

    // /api/desktop_inputs for one location, kept until the location changes, so a
    // date change does not refetch it.
    let plotInputsLocName = null;
    let plotInputsData = null;

    async function fetchPlotInputs(locName) {
        if (plotInputsData && plotInputsLocName === locName) return plotInputsData;
        const resp = await fetch('/api/desktop_inputs?location=' + encodeURIComponent(locName));
        if (!resp.ok) throw new Error(`desktop_inputs HTTP ${resp.status}`);
        const inputs = await resp.json();
        plotInputsLocName = locName;
        plotInputsData = inputs;
        return inputs;
    }

    /**
     * The page's location and object as /api/desktop_inputs describes them, or null
     * when the chart should come from the endpoint instead (engine missing, unknown
     * location, object without coordinates). Fetch errors propagate to the caller.
     */
    async function localPlotTarget() {
        if (!(window.NovaAstro && window.NovaTransit && window.NovaPlotData)) return null;
        const locName = NOVA_GRAPH_DATA.plotLocName;
        if (!locName) return null;

        const inputs = await fetchPlotInputs(locName);
        const loc = inputs.location;
        if (!loc || loc.name !== locName) return null;   // not a location this user has

        const wanted = String(NOVA_GRAPH_DATA.objectName).toLowerCase();
        const obj = (inputs.objects || []).find(o => String(o.Object).toLowerCase() === wanted);
        if (!obj || obj.ra === null || obj.dec === null) return null;

        return { inputs, loc, obj };
    }

    /**
     * The /api/get_plot_data payload from the three local scripts, or null when the
     * caller should just fetch it. Returns null only for a reason it can see
     * (engine missing, unknown location, object without coordinates); anything that
     * throws is the caller's to catch, so it fails closed onto the endpoint.
     */
    async function computeLocalPlotData(day, month, year) {
        const target = await localPlotTarget();
        if (!target) return null;
        const { inputs, loc, obj } = target;
        const date = `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;

        return window.NovaPlotData.build({
            raHours: obj.ra, decDeg: obj.dec,
            lat: loc.lat, lon: loc.lon, tz: loc.timezone, date: date,
            threshold: loc.altitude_threshold, interval: loc.sampling_interval,
            mask: loc.horizon_mask, skyglow: inputs.skyglow
        });
    }

    /**
     * One /api/get_monthly_plot_data payload per entry of `months`, in order, from
     * the local scripts, or null to fetch them all. Same contract as
     * computeLocalPlotData(): null for a visible reason, anything else throws.
     */
    async function computeLocalMonthlyPlotData(year, months) {
        const target = await localPlotTarget();
        if (!target) return null;
        const { loc, obj } = target;
        return months.map(m => window.NovaPlotData.range({
            raHours: obj.ra, decDeg: obj.dec,
            lat: loc.lat, lon: loc.lon, tz: loc.timezone,
            year: year, month: m
        }));
    }

    /** computeLocalMonthlyPlotData(), with any failure turned into null (fetch instead). */
    async function tryLocalMonthlyPlotData(year, months) {
        try {
            return await computeLocalMonthlyPlotData(year, months);
        } catch (localErr) {
            console.warn('Client-side monthly plot data failed, using the server:', localErr);
            return null;
        }
    }

    async function renderClientSideChart() {
        const chartLoadingDiv = document.getElementById('chart-loading');
        chartLoadingDiv?.classList.remove('hidden');
        const objectName = NOVA_GRAPH_DATA.objectName;
        const day = document.getElementById('day-select').value;
        const month = document.getElementById('month-select').value;
        const year = document.getElementById('year-select').value;
        const plotLat = NOVA_GRAPH_DATA.plotLat;
        const plotLon = NOVA_GRAPH_DATA.plotLon;
        const plotTz = NOVA_GRAPH_DATA.plotTz;
        const plotLocName = NOVA_GRAPH_DATA.plotLocName;
        const isOffline = !navigator.onLine;
        const apiUrl = `/api/get_plot_data/${encodeURIComponent(objectName)}?day=${day}&month=${month}&year=${year}&plot_lat=${plotLat}&plot_lon=${plotLon}&plot_tz=${encodeURIComponent(plotTz)}&plot_loc_name=${encodeURIComponent(plotLocName)}&offline=${isOffline}`;
        console.log("Fetching Chart Data from API URL:", apiUrl);
    
        try {
            if (currentTimeUpdateInterval) {
                clearInterval(currentTimeUpdateInterval);
                currentTimeUpdateInterval = null;
            }
            // Day view: compute locally when the engine is loaded, else (and on any
            // failure at all) take the endpoint's answer, which is byte-for-byte the
            // same shape this branch produces.
            let data = null;
            try {
                data = await computeLocalPlotData(day, month, year);
            } catch (localErr) {
                console.warn('Client-side plot data failed, using the server:', localErr);
                data = null;
            }
            if (!data) {
                const resp = await fetch(apiUrl);
                if (!resp.ok) throw new Error(`Failed to fetch chart data: ${resp.status} ${resp.statusText}`);
                data = await resp.json();
            }

            // Convert times to milliseconds using helper
            const labels = data.times.map(val => _convertToMilliseconds(val, plotTz));

            // Build current time annotation
            const nowMs = luxon.DateTime.now().setZone(plotTz).toMillis();
            const annotations = {};
            annotations.currentTimeLine = _buildCurrentTimeAnnotation(nowMs);
            // Build date times for calculations
            const baseDt = luxon.DateTime.fromISO(data.date, {zone: plotTz});
            const nextDt = baseDt.plus({days: 1});

            // Weather labels (used by weather overlay plugin; colours are read at draw time)
            const cloudInfo = {
                1: {label: 'Clear'},
                2: {label: 'P. Clear'},
                3: {label: 'P. Clear'},
                4: {label: 'P. Clear'},
                5: {label: 'P. Cloudy'},
                6: {label: 'P. Cloudy'},
                7: {label: 'Cloudy'},
                8: {label: 'Cloudy'},
                9: {label: 'Overcast'}
            };
            const seeingInfo = {
                1: {label: 'See: Exc'},
                2: {label: 'See: Good'},
                3: {label: 'See: Good'},
                4: {label: 'See: Avg'},
                5: {label: 'See: Avg'},
                6: {label: 'See: Poor'},
                7: {label: 'See: Poor'},
                8: {label: 'See: Bad'}
            };

            // Build sun event annotations using helper
            const sunEventResult = _buildSunEventAnnotations(data.sun_events, baseDt, nextDt);
            Object.assign(annotations, sunEventResult.annotations);
            const duskTime = sunEventResult.duskTime;
            const dawnTime = sunEventResult.dawnTime;

            // Calculate x-axis centering on midnight
            const firstMs = labels[0], lastMs = labels[labels.length - 1];
            const originalWindowMs = lastMs - firstMs;
            const midnightMs = luxon.DateTime.fromISO(data.date, {zone: plotTz}).plus({days: 1}).startOf('day').toMillis();
            const currentCenterMs = (firstMs + lastMs) / 2;
            const delta = midnightMs - currentCenterMs;
            const xMinCentered = firstMs + delta;
            const xMaxCentered = xMinCentered + originalWindowMs;

            // Build transit annotations using helper
            const transitAnnotations = _buildTransitAnnotations(data.transit_time, baseDt, duskTime, dawnTime);
            Object.assign(annotations, transitAnnotations);

            // Create night shade plugin using helper
            const nightShade = _createNightShadePlugin(duskTime, dawnTime);
            // Create chart using helper functions
            const ctx = document.getElementById('altitudeChartCanvas').getContext('2d');
            if (window.altitudeChart) window.altitudeChart.destroy();

            const datasets = _buildChartDatasets(data, labels, NOVA_GRAPH_DATA.objectName);
            const chartOptions = _buildChartOptions(annotations, plotTz, plotLocName, xMinCentered, xMaxCentered, NOVA_GRAPH_DATA.altName, data.date);

            window.altitudeChart = new Chart(ctx, {
                type: 'line',
                data: { labels, datasets },
                plugins: [nightShade, weatherOverlayPlugin],
                options: chartOptions
            });
    
            // --- NEW: Initialize weather object and start async fetch ---
            window.altitudeChart.__weather = {
                hasWeather: false,
                forecast: [],
                cloudInfo,
                seeingInfo,
                isLoading: true, // Set loading flag
                error: null
            };
            startCurrentTimeUpdater(window.altitudeChart);
    
            // Asynchronously fetch and apply weather data
            fetchAndUpdateWeather(window.altitudeChart, plotLat, plotLon, plotTz, isOffline);
            // --- END NEW ---

            // --- SECONDARY OBJECT: Restore if previously selected ---
            if (secondaryObjectName) {
                loadSecondaryObject(secondaryObjectName, secondaryObjectDisplayName);
            }
            // --- END SECONDARY OBJECT ---

        } catch (err) {
            console.error('Could not render chart:', err);
            const canvas = document.getElementById('altitudeChartCanvas'), ctx = canvas.getContext('2d');
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            ctx.font = '16px Arial'; ctx.fillStyle = 'red'; ctx.textAlign = 'center'; ctx.fillText('Error: Could not load chart data.', canvas.width / 2, canvas.height / 2);
        } finally { chartLoadingDiv?.classList.add('hidden'); }
    }
    
    async function renderMonthlyYearlyChart(view) {
        if (currentTimeUpdateInterval) {
            clearInterval(currentTimeUpdateInterval);
            currentTimeUpdateInterval = null;
        }
        const chartLoadingDiv = document.getElementById('chart-loading');
        chartLoadingDiv?.classList.remove('hidden');
        const objectName = NOVA_GRAPH_DATA.objectName, selMonth = document.getElementById('month-select').value, year = document.getElementById('year-select').value, plotLat = NOVA_GRAPH_DATA.plotLat, plotLon = NOVA_GRAPH_DATA.plotLon, plotTz = NOVA_GRAPH_DATA.plotTz;
        let titleText, objAlt = [], moonAlt = [], horizon = [];
        // Always clear weather overlay in month/year view
        try {
            if (view === 'year') {
                titleText = `Yearly Altitude at Local Midnight for ${NOVA_GRAPH_DATA.altName} - ${year}`;
                const months = [...Array(12)].map((_, i) => String(i + 1).padStart(2, '0'));
                // Computed locally when the engine is loaded, else (and on any failure)
                // the endpoint's twelve answers, which are the same shape.
                let monthly = await tryLocalMonthlyPlotData(year, months);
                if (!monthly) {
                    const urls = months.map(m => `/api/get_monthly_plot_data/${encodeURIComponent(objectName)}?year=${year}&month=${m}&plot_lat=${plotLat}&plot_lon=${plotLon}&plot_tz=${encodeURIComponent(plotTz)}`);
                    const responses = await Promise.all(urls.map(u => fetch(u)));
                    const bad = responses.find(r => !r.ok);
                    if (bad) throw new Error(`HTTP ${bad.status} for ${bad.url}`);
                    monthly = await Promise.all(responses.map(r => r.json()));
                }
                monthly.forEach(block => { block.dates.forEach((d, i) => { const t = `${d}T00:00:00`; objAlt.push({x: t, y: block.object_alt[i]}); moonAlt.push({x: t, y: block.moon_alt[i]}); horizon.push({x: t, y: 0}); }); });
            } else {
                titleText = `Monthly Altitude at Local Midnight for ${NOVA_GRAPH_DATA.altName} - ${year}-${selMonth}`;
                const local = await tryLocalMonthlyPlotData(year, [selMonth]);
                let data = local ? local[0] : null;
                if (!data) {
                    const apiUrl = `/api/get_monthly_plot_data/${encodeURIComponent(objectName)}?year=${year}&month=${selMonth}&plot_lat=${plotLat}&plot_lon=${plotLon}&plot_tz=${encodeURIComponent(plotTz)}`;
                    const resp = await fetch(apiUrl);
                    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                    data = await resp.json();
                }
                data.dates.forEach((d, i) => { const t = `${d}T00:00:00`; objAlt.push({x: t, y: data.object_alt[i]}); moonAlt.push({x: t, y: data.moon_alt[i]}); horizon.push({x: t, y: 0}); });
            }
            const ctx = document.getElementById('altitudeChartCanvas').getContext('2d');
            if (window.altitudeChart) window.altitudeChart.destroy();
            // Theme-aware colors for month/year view
            const isDark = window.stylingUtils && window.stylingUtils.isDarkTheme ? window.stylingUtils.isDarkTheme() : false;
            const gridColor = isDark ? 'rgba(150, 150, 150, 0.3)' : 'rgba(128, 128, 128, 0.5)';
            const tickColor = isDark ? '#b0b0b0' : '#666';
            const titleColor = isDark ? '#e0e0e0' : '#333';
            window.altitudeChart = new Chart(ctx, { type: 'line', data: { datasets: [ { label: `${NOVA_GRAPH_DATA.objectName} Altitude`, data: objAlt, borderColor: (window.stylingUtils && window.stylingUtils.getChartLineColor) ? window.stylingUtils.getChartLineColor(0) : '#36A2EB', borderWidth: 3, pointRadius: 0, tension: 0.2 }, { label: 'Moon Altitude', data: moonAlt, borderColor: (window.stylingUtils && window.stylingUtils.getChartLineColor) ? window.stylingUtils.getChartLineColor(1) : 'gold', borderWidth: 2.5, pointRadius: 0, tension: 0.0 }, {label: 'Horizon', data: horizon, borderColor: (window.stylingUtils && window.stylingUtils.getColor) ? window.stylingUtils.getColor('--text-primary', '#333') : '#333', borderWidth: 2, pointRadius: 0} ] }, options: { adapters: {date: {zone: plotTz}}, responsive: true, maintainAspectRatio: true, aspectRatio: 2, scales: { x: { type: 'time', time: { unit: (view === 'year') ? 'month' : 'day', displayFormats: (view === 'year') ? {month: 'MMM'} : {day: 'dd'} }, grid: { color: gridColor }, ticks: { color: tickColor }, title: { display: true, text: (view === 'year') ? `Month of ${year}` : `Day of ${year}-${selMonth}`, color: titleColor } }, y: { min: -90, max: 90, grid: { color: gridColor }, ticks: { color: tickColor }, title: {display: true, text: 'Altitude (°)', color: titleColor} } }, plugins: { legend: {position: 'right', labels: { color: tickColor }}, title: {display: false, text: titleText, align: 'start', font: {size: 16}} } } });
        } catch (err) {
            console.error(`Could not render ${view} chart:`, err);
            const canvas = document.getElementById('altitudeChartCanvas');
            if (canvas) { const c = canvas.getContext('2d'); c.clearRect(0, 0, canvas.width, canvas.height); c.font = "16px Arial"; c.fillStyle = "red"; c.textAlign = "center"; c.fillText(`Error loading ${view} chart data.`, canvas.width / 2, canvas.height / 2); }
        } finally { chartLoadingDiv?.classList.add('hidden'); }
    }
    
    let baseSurvey = null;
    let blendLayer = null;
    let geoBeltLayer = null; // Layer for satellite belt
    function to360(deg) { return ((deg % 360) + 360) % 360; }
    function toSigned180(deg) { const d = to360(deg); return d > 180 ? d - 360 : d; }
    let __pendingRotation = null;
    let __rafRotation = null;
    function scheduleRotationUpdate(deg) {
        __pendingRotation = Number(deg);
        if (__rafRotation) return;
        __rafRotation = requestAnimationFrame(() => {
            const val = __pendingRotation;
            __pendingRotation = null; __rafRotation = null;
            // Clamp to configured rotation range (0-360 by default)
            const rotation = Math.max(FRAMING_CONFIG.ROTATION_MIN, Math.min(FRAMING_CONFIG.ROTATION_MAX, Number(val) || 0));
            const sel = document.getElementById('framing-rig-select');
            if (sel && sel.selectedIndex >= 0) { const opt = sel.options[sel.selectedIndex]; updateScreenFovOverlay(opt.dataset.fovw, opt.dataset.fovh, rotation); }
            if (typeof window.updateFramingChart === 'function') window.updateFramingChart(false);
        });
    }
    function onRotationInput(val) {
        // Allow full 0-360 range to match ASIAIR PA
        let v = Number(val) || 0;
        // Normalize to configured range just in case
        v = Math.max(FRAMING_CONFIG.ROTATION_MIN, Math.min(FRAMING_CONFIG.ROTATION_MAX, ((v % 360) + 360) % 360));
    
        const span = document.getElementById('rotation-value');
        if (span) span.textContent = v.toFixed(1).replace(/\.0$/, '') + '°';
        scheduleRotationUpdate(v);
    }

    // ========================================================================
    // Private Helper Functions for openFramingAssistant()
    // ========================================================================

    /**
     * Parse query string parameters for framing assistant
     * Handles legacy survey URL mapping
     * @param {string} queryString - URL query string
     * @returns {Object} - Parsed parameters {rig, ra, dec, rot, survey, blend, blendOp, m_cols, m_rows, m_ov}
     */
    function _parseFramingQueryString(queryString) {
        const q = new URLSearchParams(queryString || '');

        // Legacy mapping for old survey URLs
        const legacyMapping = {
            "https://www.simg.de/nebulae3/dr0_1/hbr8": "https://www.simg.de/nebulae3/dr0_2/hbr8",
            "https://www.simg.de/nebulae3/dr0_1/halpha8": "https://www.simg.de/nebulae3/dr0_2/halpha8",
            "https://www.simg.de/nebulae3/dr0_1/tc8": "https://www.simg.de/nebulae3/dr0_2/rgb8"
        };

        let survey = q.get('survey');
        if (legacyMapping[survey]) {
            console.log("Upgrading legacy survey URL:", survey);
            survey = legacyMapping[survey];
        }

        let blend = q.get('blend');
        if (legacyMapping[blend]) {
            blend = legacyMapping[blend];
        }

        const result = {
            rig: q.get('rig'),
            rigUid: q.get('rig_uid'),
            ra: parseFloat(q.get('ra')),
            dec: parseFloat(q.get('dec')),
            rot: parseFloat(q.get('rot')),
            survey: survey,
            blend: blend,
            blendOp: parseFloat(q.get('blend_op')),
            m_cols: q.get('m_cols'),
            m_rows: q.get('m_rows'),
            m_ov: q.get('m_ov'),
            // Image Adjustment params
            img_brightness: parseFloat(q.get('img_b')),
            img_contrast: parseFloat(q.get('img_c')),
            img_gamma: parseFloat(q.get('img_g')),
            img_saturation: parseFloat(q.get('img_s')),
            // Overlay Preference params
            geo_belt_enabled: q.get('geo_belt') === '1' ? true : null
        };

        return result;
    }

    /**
     * List the saved framing's own rig in the modal select even when it is
     * inactive: the server renders active rigs only, but a framing that already
     * points at an inactive rig must still show and select it. Options added
     * here carry data-injected-inactive-rig and are dropped again on the next
     * open.
     */
    function _ensureInactiveRigOption(sel, params) {
        if (!sel) return;
        sel.querySelectorAll('option[data-injected-inactive-rig]')
           .forEach(o => o.remove());
        if (!params || !params.rigUid) return;
        if (Array.from(sel.options).some(o => o.dataset.rigUid === params.rigUid)) return;
        const rig = (window.availableRigs || []).find(r => (r.rig_uid || '') === params.rigUid);
        if (!rig || rig.active) return;
        const opt = document.createElement('option');
        opt.value = rig.rig_id;
        opt.dataset.rigUid = rig.rig_uid || '';
        opt.dataset.fovw = rig.fov_w_arcmin;
        opt.dataset.fovh = rig.fov_h_arcmin;
        opt.dataset.injectedInactiveRig = '1';
        opt.textContent = `${rig.rig_name} (${sel.dataset.inactiveLabel})`;
        sel.appendChild(opt);
        sel.value = opt.value;
    }

    /**
     * Restore framing assistant state from parsed query parameters
     * @param {Object} params - Parsed query parameters
     * @param {Function} setSurvey - Function to set survey
     * @param {Function} ensureBlendLayer - Function to ensure blend layer exists
     * @param {Function} setBlendOpacity - Function to set blend opacity
     * @returns {Object} - State flags {haveCenter, haveRot, haveRigRestored}
     */
    function _restoreFramingState(params, setSurvey, ensureBlendLayer, setBlendOpacity) {
        const flags = {
            haveCenter: false,
            haveRot: false,
            haveRigRestored: false
        };

        // Restore rig selection — the record_uid alone decides, never the row number
        if (params.rigUid) {
            const sel = document.getElementById('framing-rig-select');
            if (sel) {
                const idx = Array.from(sel.options).findIndex(o => o.dataset.rigUid === params.rigUid);
                if (idx >= 0) {
                    sel.selectedIndex = idx;
                    flags.haveRigRestored = true;
                }
            }
        }

        // Restore rotation
        if (!Number.isNaN(params.rot)) {
            const rotInput = document.getElementById('framing-rotation');
            const normRot = (params.rot % 360 + 360) % 360;
            if (rotInput) rotInput.value = normRot;
            const rotSpan = document.getElementById('rotation-value');
            if (rotSpan) rotSpan.textContent = `${Math.round(normRot)}°`;
            flags.haveRot = true;
        }

        // Restore survey. setSurvey reports its own failures (it names the survey in
        // the notice), so there is nothing to catch here: it never rejects.
        if (params.survey) {
            const sSel = document.getElementById('survey-select');
            if (sSel) sSel.value = params.survey;
            if (typeof setSurvey === 'function') setSurvey(params.survey);
        }

        // Restore blend settings
        try {
            const bsel = document.getElementById('blend-survey-select');
            const bop = document.getElementById('blend-opacity');
            if (params.blend && bsel) {
                bsel.value = params.blend;
                if (typeof ensureBlendLayer === 'function') ensureBlendLayer();
            }
            if (!Number.isNaN(params.blendOp) && bop) {
                bop.value = String(Math.max(0, Math.min(1, params.blendOp)));
                if (typeof setBlendOpacity === 'function') setBlendOpacity(bop.value);
            }
        } catch (e) {
            console.warn('[nova] Blend restore error:', e);
        }

        // Restore mosaic settings
        if (params.m_cols) document.getElementById('mosaic-cols').value = params.m_cols;
        if (params.m_rows) document.getElementById('mosaic-rows').value = params.m_rows;
        if (params.m_ov) document.getElementById('mosaic-overlap').value = params.m_ov;

        // Restore image adjustment settings
        // Store values to apply AFTER survey is loaded
        const pendingImageAdjustments = {
            brightness: !Number.isNaN(params.img_brightness) ? params.img_brightness : null,
            contrast: !Number.isNaN(params.img_contrast) ? params.img_contrast : null,
            gamma: !Number.isNaN(params.img_gamma) ? params.img_gamma : null,
            saturation: !Number.isNaN(params.img_saturation) ? params.img_saturation : null
        };

        // Set slider values immediately
        let hasImageAdjustments = false;
        if (pendingImageAdjustments.brightness !== null) {
            const el = document.getElementById('img-bright');
            if (el) { el.value = pendingImageAdjustments.brightness; hasImageAdjustments = true; }
        }
        if (pendingImageAdjustments.contrast !== null) {
            const el = document.getElementById('img-contrast');
            if (el) { el.value = pendingImageAdjustments.contrast; hasImageAdjustments = true; }
        }
        if (pendingImageAdjustments.gamma !== null) {
            const el = document.getElementById('img-gamma');
            if (el) { el.value = pendingImageAdjustments.gamma; hasImageAdjustments = true; }
        }
        if (pendingImageAdjustments.saturation !== null) {
            const el = document.getElementById('img-sat');
            if (el) { el.value = pendingImageAdjustments.saturation; hasImageAdjustments = true; }
        }

        // Store for deferred application (will be called after Aladin is ready)
        if (hasImageAdjustments) {
            flags.pendingImageAdjustments = pendingImageAdjustments;
        }

        // Restore Geo Belt checkbox state (default ON if not specified)
        const geoBeltCheckbox = document.getElementById('show-geo-belt');
        if (geoBeltCheckbox) {
            // Only override if explicitly set in params; otherwise keep current/default state
            if (params.geo_belt_enabled !== undefined && params.geo_belt_enabled !== null) {
                geoBeltCheckbox.checked = params.geo_belt_enabled;
            }
        }

        // Restore center coordinates
        if (!Number.isNaN(params.ra) && !Number.isNaN(params.dec)) {
            flags.haveCenter = true;
        }

        return flags;
    }

    async function openFramingAssistant(optionalQueryString) {

        // Reset any stuck scan state on open
        const _scanBtn = document.getElementById('scan-frame-btn');
        if (_scanBtn) {
            _scanBtn.textContent = window.t('scan_frame');
            _scanBtn.disabled    = false;
        }
        if (!scanFrameActive) scanResultsCatalog = null;
        updateScanButtonState();
        setTimeout(updateScanButtonState, 600);

        const _sizeEl = document.getElementById('scan-min-size');
        if (_sizeEl && !_sizeEl.dataset.listenerAttached) {
            _sizeEl.dataset.listenerAttached = 'true';
            _sizeEl.addEventListener('input', function() {
                applyMinSizeFilter(parseFloat(this.value) || 0);
            });
        }

        // --- Check for internet connection first ---
        // With the offline-sky proxy on, cached tiles are served locally, so open
        // the modal and let the notice fire only if the base layer actually fails.
        if (!navigator.onLine && !window.NOVA_OFFLINE_SKY) {
            displayOfflineMessage('aladin-lite-div', window.t('framing_requires_internet'));
            return; // Stop execution if offline
        }

        const framingRigSelect = document.getElementById('framing-rig-select');
        let _rigParams = null;
        try { _rigParams = _parseFramingQueryString(optionalQueryString || location.search); }
        catch (e) { console.warn('[nova] Query parsing error:', e); }
        _ensureInactiveRigOption(framingRigSelect, _rigParams);
        if (framingRigSelect.options.length === 0 || framingRigSelect.value === "") {
            await novaAlert(window.t('no_rigs_configured'));
            return;
        }

        // --- CLEANUP: Clear existing overlays before re-initializing ---
        if (fovLayer && aladin) {
            try {
                fovLayer.removeAll();
            } catch (e) {
                console.warn('[FRAMING] Could not clear FOV overlay:', e);
            }
        }
        // Clear geo belt layer to prevent ghost overlays
        if (geoBeltLayer && aladin) {
            try {
                if (aladin.removeCatalog) aladin.removeCatalog(geoBeltLayer);
                if (aladin.removeOverlay) aladin.removeOverlay(geoBeltLayer);
                geoBeltLayer = null;
            } catch (e) {
                console.warn('[FRAMING] Could not clear Geo Belt overlay:', e);
            }
        }
        // --- END CLEANUP ---

        (function wireRotationReset(){ const span = document.getElementById('rotation-value'); if (!span || span.dataset.resetWired) return; span.dataset.resetWired = '1'; span.addEventListener('click', () => { const slider = document.getElementById('framing-rotation'); if (!slider) return; slider.value = '0'; slider.dispatchEvent(new Event('input', { bubbles: true })); }); })();

        // Is CDS reachable? If not, which DSS2 mirror answers? Decides the base layer below
        // (mirror, else NSNS, when CDS is down)
        resetSurveyNotice();
        // Placeholder only when Aladin still has to be created (not over an existing view)
        if (!aladin) showSurveyLoading();
        if (window.NOVA_OFFLINE_SKY) {
            // No browser-side CDS probe: the proxy handles failover itself, and
            // the "server unreachable" notice comes only from the base layer's
            // errorCallback, never from browser-to-CDS reachability.
            cdsReachable = true;
            dssMirror = null;
        } else {
            ({ cdsReachable, dssMirror } = await probeSurveySources());
        }

        if (!aladin) {
            try {
                // DSS2 by default; NSNS instead while CDS is unreachable (northern objects).
                // On the current mode's root, like every other layer.
                const initialBase = chooseDisplayedBase("P/DSS2/color");
                const initialLayer = createSurveyLayer('base', initialBase.survey,
                                                       resolveHipsId(initialBase.survey));
                baseWanted = initialBase.survey;
                aladin = A.aladin('#aladin-lite-div', {
                    survey: initialLayer,
                    fov: FRAMING_CONFIG.DEFAULT_FOV_DEG,
                    cooFrame: 'ICRS',
                    showFullscreenControl: false,
                    showGotoControl: false,
                    showLayersControl: false,
                    log: false
                });
                guardAladinFallback(aladin);
                hideSurveyLoading();
                if (initialBase.notice) showSurveyNotice();
            (function installSlowWheelZoom(){ if (window.__novaSlowZoomInstalled) return; const host = document.getElementById('aladin-lite-div'); if (!host) return; function onWheel(ev) { if (ev.ctrlKey) return; ev.preventDefault(); ev.stopPropagation(); if (!aladin) return; const unit = (ev.deltaMode === 1) ? 16 : (ev.deltaMode === 2) ? 400 : 1; let dy = (ev.deltaY || 0) * unit; dy = Math.max(-80, Math.min(80, dy)); const g = aladin.getFov(), current = Array.isArray(g) ? (g[0] ?? 1) : (g ?? 1); const scale = Math.exp(dy * 0.00075), minFov = 0.01, maxFov = 180; const next = Math.min(maxFov, Math.max(minFov, current * scale)); if (Number.isFinite(next)) aladin.setFov(next); } host.addEventListener('wheel', onWheel, { passive: false, capture: true }); const tryBindCanvas = () => { const cv = host.querySelector('canvas'); if (cv) cv.addEventListener('wheel', onWheel, { passive: false, capture: true }); }; tryBindCanvas(); setTimeout(tryBindCanvas, 50); window.__novaSlowZoomInstalled = true; })();
            baseSurvey = aladin.getBaseImageLayer();
            (function wireBlendAndConstellationUI(){ const blendSel = document.getElementById('blend-survey-select'), blendOp = document.getElementById('blend-opacity'); if (blendSel) blendSel.addEventListener('change', () => { if (blendSel.value && blendOp.value === '0') { blendOp.value = '0.5'; } ensureBlendLayer(); const v = Number(blendOp?.value || 0); setBlendOpacity(v); updateSurveyCredit(); updateFramingChart(false); }); if (blendOp) { const sync = (e) => { setBlendOpacity(e.target.value); updateSurveyCredit(); updateFramingChart(false); }; blendOp.addEventListener('input', sync); blendOp.addEventListener('change', sync); } try { if (blendOp) setBlendOpacity(blendOp.value); } catch(e) {} })();
            if (aladin.on) aladin.on('zoomChanged', () => { const sel = document.getElementById('framing-rig-select'), rot = parseFloat(document.getElementById('framing-rotation')?.value || '0') || 0; if (sel && sel.selectedIndex >= 0) { const opt = sel.options[sel.selectedIndex]; updateScreenFovOverlay(opt.dataset.fovw, opt.dataset.fovh, rot); } });
            if (aladin.on) aladin.on('baseLayerChanged', () => { try { const bop = document.getElementById('blend-opacity'); ensureBlendLayer(); if (bop) setBlendOpacity(bop.value); } catch (e) { console.warn('[nova] Could not reapply blend after base change:', e); } });
            fovLayer = A.graphicOverlay({color: (window.stylingUtils && window.stylingUtils.getPrimaryColor) ? window.stylingUtils.getPrimaryColor() : '#83b4c5', lineWidth: 3});
            aladin.addOverlay(fovLayer);
            const canvas = document.getElementById('aladin-lite-div');
            if (window.ResizeObserver && canvas) { let roTimer = null; const ro = new ResizeObserver(() => { clearTimeout(roTimer); roTimer = setTimeout(() => { const sel = document.getElementById('framing-rig-select'); const rot = parseFloat(document.getElementById('framing-rotation')?.value || '0') || 0; if (sel && sel.selectedIndex >= 0) { const opt = sel.options[sel.selectedIndex]; applyRigFovZoom(opt.dataset.fovw, opt.dataset.fovh, rot); } updateFramingChart(false); }, 80); }); ro.observe(canvas); }
            (function ensureScreenOverlay(){ const host = document.getElementById('aladin-lite-div'); if (!host || document.getElementById('screen-fov-overlay')) return; const ov = document.createElement('div'); ov.id = 'screen-fov-overlay'; ov.className = 'screen-fov-overlay'; const rect = document.createElement('div'); rect.id = 'screen-fov-rect'; rect.className = 'screen-fov-rect'; rect.hidden = true; ov.appendChild(rect); host.appendChild(ov); })();
            (function wireRotationLiveUpdate(){ const rotInput = document.getElementById('framing-rotation'); if (!rotInput) return; const handler = () => { const raw = (typeof rotInput.valueAsNumber === 'number') ? rotInput.valueAsNumber : parseFloat(rotInput.value) || 0; const snapped = (Math.abs(raw) <= 1) ? 0 : raw; if (snapped !== raw) rotInput.value = String(snapped); updateFramingChart(false); }; try { rotInput.removeEventListener('input', rotInput.__novaRotHandler); } catch(e) {} try { rotInput.removeEventListener('change', rotInput.__novaRotHandler); } catch(e) {} rotInput.__novaRotHandler = handler; rotInput.addEventListener('input', handler); rotInput.addEventListener('change', handler); try { rotInput.setAttribute('value', String(rotInput.valueAsNumber ?? rotInput.value ?? 0)); } catch(e) {} handler(); })();
            (function wireInsertIntoProject(){ const btn = document.getElementById('insert-into-project'); if (!btn) return; try { btn.removeEventListener('click', btn.__novaInsertHandler); } catch(e) {} btn.__novaInsertHandler = (ev) => { try { const q = buildFramingQuery(), href = location.pathname + q; history.replaceState(null, '', href); } catch(e) { console.warn('[nova] Insert-to-project wiring error:', e); } }; btn.addEventListener('click', btn.__novaInsertHandler); })();
            } catch (initErr) {
                console.error('[FRAMING] Aladin initialization failed:', initErr);
                displayOfflineMessage('aladin-lite-div', window.t('framing_init_failed'));
                return;
            }
        }
    
        // Parse query string and restore state using helpers
        let params, flags;
        try {
            params = _parseFramingQueryString(optionalQueryString || location.search);
            flags = _restoreFramingState(params, setSurvey, ensureBlendLayer, setBlendOpacity);
        } catch (e) {
            console.warn('[nova] Query parsing error:', e);
            params = {};
            flags = {haveCenter: false, haveRot: false, haveRigRestored: false};
        }

        const haveCenter = flags.haveCenter;
        const haveRot = flags.haveRot;
        const haveRigRestored = flags.haveRigRestored;

        // No saved survey set the base layer: re-check the one shown (or still loading),
        // since CDS may have gone down or come back since a cached Aladin instance was
        // built.
        if (!params.survey) {
            const selVal = document.getElementById('survey-select')?.value;
            const choice = selVal ? chooseDisplayedBase(selVal) : null;
            if (choice && choice.survey !== baseWanted) setSurvey(selVal);
            else if (choice && choice.notice) showSurveyNotice();
        }

        // Ensure blend layer is applied
        try {
            const bop = document.getElementById('blend-opacity');
            ensureBlendLayer();
            if (bop) setBlendOpacity(bop.value);
        } catch (e) {
            console.warn('[nova] Blend layer setup error:', e);
        }
        updateSurveyCredit();

        // Offline sky UI: show the controls, mark surveys, and kick off the
        // low-res base fetch for the surveys actually shown.
        wireOfflineSkyUi();

        // Set default rotation if not restored
        if (!haveRot) {
            const rotInput = document.getElementById('framing-rotation');
            if (rotInput) rotInput.value = 0;
            const rotSpan = document.getElementById('rotation-value');
            if (rotSpan) rotSpan.textContent = `0°`;
        }
        // A saved centre: the view goes there (the frame is always the view centre)
        if (haveCenter) {
            if (aladin && typeof aladin.gotoRaDec === 'function') aladin.gotoRaDec(params.ra, params.dec);
            const sel = document.getElementById('framing-rig-select');
            const rot = parseFloat(document.getElementById('framing-rotation')?.value || '0') || 0;
            if (sel && sel.selectedIndex >= 0) { const opt = sel.options[sel.selectedIndex]; applyRigFovZoom(opt.dataset.fovw, opt.dataset.fovh, rot); }
        }
        updateFramingChart(haveCenter ? false : true);
        updateFovVsObjectLabel?.();
        updateReadoutFromCenter?.();

        try {
            ensureNovaObjectsCatalog();
        } catch (e) {
            console.error('Failed to build Nova objects catalog for Aladin:', e);
        }

        showFrameOverlay();

        // Apply deferred image adjustments after Aladin is ready
        // Use setTimeout to ensure survey layer is fully loaded
        if (flags.pendingImageAdjustments) {
            setTimeout(() => {
                if (typeof window.updateImageAdjustments === 'function') {
                    window.updateImageAdjustments();
                }
            }, 500);  // 500ms delay to ensure survey is loaded
        }

        // Restore Geo Belt State (respect saved preference)
        const geoCheck = document.getElementById('show-geo-belt');
        if (geoCheck) {
            // Call toggleGeoBelt with the current checkbox state (already restored from saved value)
            toggleGeoBelt(geoCheck.checked);
        }
    }
    // ========================================================================
    // Framing tab: shown/hidden by showTab (graph_view.js) via window.novaFramingTab
    // ========================================================================
    let framingPageLoaded = false;   // set by the load handler; the first open waits for it
    let framingOpening = null;       // the first open while it runs; never two at once

    function isFramingTabVisible() {
        const tab = document.getElementById('framing-tab');
        return !!tab && tab.classList.contains('active');
    }

    /**
     * The one open of the page: a framing URL (rig + ra/dec) opens with its own state,
     * otherwise the saved framing when there is one (this also serves ?openframing=1),
     * otherwise the defaults.
     */
    async function openFramingTabFirstTime() {
        const q = new URLSearchParams(location.search);
        if (q.has('rig') && (q.has('ra') || q.has('dec'))) return openFramingAssistant();
        let qs = '';
        try {
            const r = await fetch(`/api/get_framing/${encodeURIComponent(NOVA_GRAPH_DATA.objectName)}`);
            const data = await r.json();
            setSavedFraming(data);   // the Result shows the saved values before Aladin loads
            if (data.status === 'found') qs = savedFramingQuery(data);
        } catch (e) {
            console.warn('[FRAMING] Could not load the saved framing:', e);
        }
        return openFramingAssistant(qs);
    }

    function onFramingTabShown() {
        if (!framingPageLoaded) return;   // the load handler calls this again once the page is ready
        // Not open yet (first show, or an earlier open stopped: offline, no rigs): open
        if (!aladin) {
            if (!framingOpening) framingOpening = openFramingTabFirstTime().finally(() => { framingOpening = null; });
            return;
        }
        if (framingOpening) return;   // the running open draws the frame and starts the loop
        // Shown again: Aladin exists; give it its size back and resume the frame overlay
        requestAnimationFrame(() => {
            if (!aladin) return;
            try { aladin.view?.fixLayoutDimensions?.(); } catch (e) {}
            updateFramingChart(false);
            startFrameOverlayLoop();
            updateScanButtonState();
        });
    }

    /** Open saved framing while the first open still runs waits for it, so Aladin is
     *  created once: both opens would otherwise find aladin null and each create one. */
    function openFramingQueued(qs) {
        if (framingOpening) return framingOpening.then(() => openFramingAssistant(qs));
        return openFramingAssistant(qs);
    }

    function onFramingTabHidden() {
        stopFrameOverlayLoop();
    }

    function flipFraming90() { const slider = document.getElementById('framing-rotation'); let v = parseFloat(slider.value) || 0; v += 90; const utils = fu(); v = (utils.normalizeAngle ? utils.normalizeAngle(v) : ((v % 360) + 360) % 360); slider.value = v; slider.dispatchEvent(new Event('input', { bubbles: true })); updateFramingChart(false); if (typeof updateReadoutFromCenter === 'function') updateReadoutFromCenter(); }
    
    function toggleGeoBelt(show) {
        if (!aladin) return;

        // 1. Cleanup (Handle both Overlay and Catalog types safely)
        if (geoBeltLayer) {
            try {
                // Try removing as catalog first
                if (aladin.removeCatalog) aladin.removeCatalog(geoBeltLayer);
                // Fallback for overlays
                if (aladin.removeOverlay) aladin.removeOverlay(geoBeltLayer);
            } catch (e) {
                console.warn("[Nova] Cleanup warning:", e);
            }
            geoBeltLayer = null;
        }

        if (!show) return;

        try {
            // Use framing utilities if available
            const utils = fu();
            let apparentDec, raPoints;

            if (utils.calculateGeoBeltDeclination && utils.generateGeoBeltRaPoints) {
                // 2. Calculate Declination using utils
                const rawLat = window.NOVA_GRAPH_DATA.plotLat || 0;
                const latDeg = parseFloat(rawLat) || 0;
                apparentDec = utils.calculateGeoBeltDeclination(latDeg);
                if (isNaN(apparentDec)) apparentDec = 0;

                console.log(`[Nova] Drawing Geo Belt (Catalog) at Dec: ${apparentDec.toFixed(2)}°`);

                // 3. Create CATALOG Layer
                geoBeltLayer = A.catalog({
                    name: 'Geostationary Belt',
                    color: (window.stylingUtils && window.stylingUtils.getGeostationaryColor) ? window.stylingUtils.getGeostationaryColor() : '#e056fd',
                    sourceSize: 6,   // Safe minimum size to prevent canvas crash
                    shape: 'circle'
                });
                aladin.addCatalog(geoBeltLayer);

                // 4. Generate High-Density Points (Simulates a line)
                raPoints = utils.generateGeoBeltRaPoints();
            } else {
                // Fallback to original calculation
                const rawLat = window.NOVA_GRAPH_DATA.plotLat || 0;
                const latDeg = parseFloat(rawLat) || 0;

                const latRad = latDeg * Math.PI / 180;
                const Re = 6378;
                const Rgeo = 42164;
                const num = Re * Math.sin(latRad);
                const den = Rgeo - (Re * Math.cos(latRad));
                const parallaxRad = Math.atan2(num, den);

                apparentDec = -(parallaxRad * 180 / Math.PI);
                if (isNaN(apparentDec)) apparentDec = 0;

                console.log(`[Nova] Drawing Geo Belt (Catalog) at Dec: ${apparentDec.toFixed(2)}°`);

                // 3. Create CATALOG Layer
                geoBeltLayer = A.catalog({
                    name: 'Geostationary Belt',
                    color: (window.stylingUtils && window.stylingUtils.getGeostationaryColor) ? window.stylingUtils.getGeostationaryColor() : '#e056fd',
                    sourceSize: 6,   // Safe minimum size to prevent canvas crash
                    shape: 'circle'
                });
                aladin.addCatalog(geoBeltLayer);

                // 4. Generate High-Density Points (Simulates a line)
                raPoints = [];
                // Create a point every 0.2 degrees (High density)
                for (let ra = 0; ra < 360; ra += 0.2) {
                    raPoints.push(ra);
                }
            }

            // Convert RA values to proper Aladin Source objects
            const sources = raPoints.map(ra => A.source(ra, apparentDec, {}));
            geoBeltLayer.addSources(sources);

        } catch (e) {
            console.error("[Nova] Critical Error in toggleGeoBelt:", e);
        }
    }
    
    /** The whole frame in degrees: one pane grown by the mosaic, overlap counted.
     *  cols/rows/overlapPct fall back to the inputs (1x1, 10%). One frame source:
     *  the fit and the overlay rectangle describe this same extent, and it is the
     *  maths the offline download page will use for the frame it asks the server
     *  to keep. */
    function frameExtentDeg(paneWDeg, paneHDeg, cols, rows, overlapPct) {
        const c = Math.max(1, parseInt(cols, 10) || 1);
        const r = Math.max(1, parseInt(rows, 10) || 1);
        const ov = (Math.min(80, Math.max(0, parseFloat(overlapPct) || 0))) / 100;
        return { w: paneWDeg * (1 + (c - 1) * (1 - ov)), h: paneHDeg * (1 + (r - 1) * (1 - ov)) };
    }
    /** Fit the view to the whole frame -- every pane of a mosaic, at its rotation.
     *
     *  fovW/fovH may be the pane size (arcmin) for the callers that have only that;
     *  whatever is passed is treated as the frame the mosaic grows from, so the
     *  fit and the overlay rectangle always describe the same rectangle.
     */
    function applyRigFovZoom(fovW_arcmin, fovH_arcmin, rotationDeg = 0, margin = 1.06) {
        if (!aladin) return; const host = document.getElementById('aladin-lite-div'); if (!host) return;
        const wpx = host.clientWidth, hpx = host.clientHeight; if (!(wpx > 0 && hpx > 0)) return; const aspect = wpx / hpx;
        const paneWDeg = parseFloat(fovW_arcmin) / 60, paneHDeg = parseFloat(fovH_arcmin) / 60; if (!(isFinite(paneWDeg) && isFinite(paneHDeg) && paneWDeg > 0 && paneHDeg > 0)) return;
        const frame = frameExtentDeg(paneWDeg, paneHDeg,
                                     document.getElementById('mosaic-cols')?.value,
                                     document.getElementById('mosaic-rows')?.value,
                                     document.getElementById('mosaic-overlap')?.value);
        const th = (parseFloat(rotationDeg) || 0) * Math.PI / 180;
        const needWidthDeg = Math.abs(frame.w * Math.cos(th)) + Math.abs(frame.h * Math.sin(th)), needHeightDeg = Math.abs(frame.w * Math.sin(th)) + Math.abs(frame.h * Math.cos(th));
        const requiredWidthDeg = Math.max(needWidthDeg * margin, needHeightDeg * margin * aspect);
        aladin.setFov(requiredWidthDeg);
        updateScreenFovOverlay(fovW_arcmin, fovH_arcmin, rotationDeg);
    }
    window.updateFramingChart = function (recenter = true) {
        if (!aladin) return;
        const objectName = NOVA_GRAPH_DATA.objectName, framingRigSelect = document.getElementById('framing-rig-select'), rotationSlider = document.getElementById('framing-rotation'), selectedOption = framingRigSelect.options[framingRigSelect.selectedIndex];
        if (!selectedOption) return;
        const fovWidthArcmin = parseFloat(selectedOption.dataset.fovw), fovHeightArcmin = parseFloat(selectedOption.dataset.fovh);
        const vNum = (rotationSlider && typeof rotationSlider.valueAsNumber === 'number') ? rotationSlider.valueAsNumber : NaN;
        const rotation = Number.isFinite(vNum) ? vNum : (Number.isFinite(parseFloat(rotationSlider.value)) ? parseFloat(rotationSlider.value) : 0);

        // Rotation badge, the Result angle and Copy angle: one value, whole degrees as always
        (function updateRotationBadge(){ const el = document.getElementById('rotation-value'), sliderEl = document.getElementById('framing-rotation'), angleEl = document.getElementById('result-angle'), txt = `${Math.round(rotation)}°`; if (el) el.textContent = txt; if (angleEl) angleEl.value = txt; if (sliderEl) sliderEl.title = window.t('framing_rotation_title', { angle: txt }); })();

        if (recenter) applyRigFovZoom(fovWidthArcmin, fovHeightArcmin, rotation);
        if (recenter) {
            // The object's own coordinates from the database when there are any, else a name lookup
            const raDeg = window.NOVA_GRAPH_DATA.objectRADeg;
            const decDeg = window.NOVA_GRAPH_DATA.objectDECDeg;
            const goneThere = () => {
                applyRigFovZoom(fovWidthArcmin, fovHeightArcmin, rotation);
                showFrameOverlay();
            };
            if (raDeg != null && decDeg != null && isFinite(raDeg) && isFinite(decDeg)) {
                aladin.gotoRaDec(raDeg, decDeg);
                goneThere();
            } else {
                aladin.gotoObject(objectName, { success: goneThere, error: () => showFrameOverlay() });
            }
            return;
        }
        if (fovLayer) fovLayer.removeAll();
        updateScreenFovOverlay(fovWidthArcmin, fovHeightArcmin, rotation);
        updateResultDiffers();
    };
    let frameRafId = null;

    // --- North on screen ----------------------------------------------------
    // Aladin's drag keeps the grabbed sky point exactly under the cursor. That
    // turn of the sphere does not keep north up: every drag rolls the view by
    // about dRA * sin(dec), and drags in loops add up (three circles at a 180 deg
    // field: ~40 deg). gotoRaDec/gotoObject keep the roll. The frame overlay is
    // screen-fixed, so it is drawn at Angle + where north points at the view
    // centre (every frame, during the drag and its inertia too), and once the
    // view has come to rest it is turned back to north-up around its centre.
    let viewPointerDown = false;     // a mouse button / finger is down on the sky
    let viewStill = { ra: NaN, dec: NaN, frames: 0 };
    const VIEW_LEVEL_STILL_FRAMES = 6;

    // Where north points on screen at the view centre, in degrees clockwise from
    // screen-up (0 = north up). Measured with Aladin's own projection: the screen
    // direction from the centre to a point a little north of it (south of it at
    // the pole). Falls back to Aladin's roll angle, else 0.
    function viewNorthOffsetDeg() {
        if (!aladin) return 0;
        try {
            const c = typeof aladin.world2pix === 'function' ? aladin.getRaDec() : null;
            if (c && Number.isFinite(c[0]) && Number.isFinite(c[1])) {
                const gf = aladin.getFov();
                const fov = Array.isArray(gf) ? gf[0] : gf;
                const d = Math.min(0.5, Math.max(1e-4, 0.01 * (Number(fov) || 1)));
                const towardsSouth = c[1] + d > 89.9999;
                const p0 = aladin.world2pix(c[0], c[1]);
                const p1 = aladin.world2pix(c[0], towardsSouth ? c[1] - d : c[1] + d);
                if (p0 && p1 && (p1[0] !== p0[0] || p1[1] !== p0[1])) {
                    let deg = Math.atan2(p1[0] - p0[0], -(p1[1] - p0[1])) * 180 / Math.PI;
                    if (towardsSouth) deg += 180;
                    return ((deg + 540) % 360) - 180;
                }
            }
        } catch (e) { /* fall through */ }
        if (typeof aladin.getRotation === 'function') {
            const r = Number(aladin.getRotation());
            if (Number.isFinite(r)) return -r;
        }
        return 0;
    }

    // Turn the view back to north-up once it is at rest: no button or finger
    // down, and the centre unchanged for a few frames (inertia over). Turned
    // around the centre, so the centre -- and the frame on the sky -- stay put.
    function maybeLevelView() {
        if (!aladin || viewPointerDown || typeof aladin.setRotation !== 'function'
            || typeof aladin.getRotation !== 'function') {
            viewStill.frames = 0;
            return;
        }
        const rc = aladin.getRaDec();
        if (rc[0] === viewStill.ra && rc[1] === viewStill.dec) viewStill.frames++;
        else viewStill = { ra: rc[0], dec: rc[1], frames: 0 };
        if (viewStill.frames < VIEW_LEVEL_STILL_FRAMES) return;
        if (Math.abs(Number(aladin.getRotation()) || 0) < 1e-3) return;
        aladin.setRotation(0);
    }

    // Track whether the sky is being held (mouse or touch), on the window so a
    // release outside the view counts too.
    function wireViewPointer(host) {
        if (!host || host.__novaPointerWired) return;
        host.__novaPointerWired = true;
        host.addEventListener('pointerdown', () => { viewPointerDown = true; }, true);
        const up = () => { viewPointerDown = false; };
        window.addEventListener('pointerup', up, true);
        window.addEventListener('pointercancel', up, true);
    }
    function updateScreenFovOverlay(fovWidthArcmin, fovHeightArcmin, rotationDeg) {
        const host = document.getElementById('aladin-lite-div');
        const rectEl = document.getElementById('screen-fov-rect');
        if (!host || !rectEl || !aladin) return;

        const cols = parseInt(document.getElementById('mosaic-cols')?.value || 1);
        const rows = parseInt(document.getElementById('mosaic-rows')?.value || 1);
        const overlap = parseFloat(document.getElementById('mosaic-overlap')?.value || 0) / 100;

        const wpx = host.clientWidth || 1;
        const hpx = host.clientHeight || 1;

        const gf = aladin.getFov();
        const viewWdeg = Array.isArray(gf) ? (gf[0] ?? 1) : (gf ?? 1);
        const viewHdeg = viewWdeg * (hpx / wpx);

        const fovWdeg = (parseFloat(fovWidthArcmin) || 0) / 60;
        const fovHdeg = (parseFloat(fovHeightArcmin) || 0) / 60;
        if (!(fovWdeg > 0 && fovHdeg > 0)) return;

        // Pane size in pixels and the step between pane centres
        const panePxW = (fovWdeg / viewWdeg) * wpx;
        const panePxH = (fovHdeg / viewHdeg) * hpx;
        const stepW = panePxW * (1 - overlap);
        const stepH = panePxH * (1 - overlap);

        // rectEl is a 0×0 point in the centre of the view, rotated; the panes sit around it.
        // Geometry only, through custom properties read by .screen-fov-rect / .screen-fov-pane.
        rectEl.hidden = false;
        // The Angle is relative to north; the view may be rolled (see viewNorthOffsetDeg).
        rectEl.style.setProperty('--frame-rot', `${(Number(rotationDeg) || 0) + viewNorthOffsetDeg()}deg`);
        rectEl.style.setProperty('--pane-w', panePxW + 'px');
        rectEl.style.setProperty('--pane-h', panePxH + 'px');

        const isMosaic = cols > 1 || rows > 1;
        const want = cols * rows;
        while (rectEl.children.length > want) rectEl.removeChild(rectEl.lastChild);
        while (rectEl.children.length < want) rectEl.appendChild(document.createElement('div'));

        // Grid (0,0) is bottom-left: r = 0 maps to the largest screen Y
        let k = 0;
        for (let r = 0; r < rows; r++) {
            for (let c = 0; c < cols; c++) {
                const div = rectEl.children[k++];
                div.className = isMosaic ? 'screen-fov-pane is-mosaic' : 'screen-fov-pane';
                div.textContent = isMosaic ? `${c+1},${r+1}` : '';
                div.style.setProperty('--pane-x', ((c - (cols - 1) / 2) * stepW) + 'px');
                div.style.setProperty('--pane-y', (-1 * (r - (rows - 1) / 2) * stepH) + 'px');
            }
        }
    }
    // While the Framing tab shows: the frame overlay follows zoom and resize, the Result
    // follows the view centre as the sky is dragged.
    function startFrameOverlayLoop() { if (frameRafId) return; wireViewPointer(document.getElementById('aladin-lite-div')); const tick = () => { if (!aladin) { frameRafId = null; return; } maybeLevelView(); const sel = document.getElementById('framing-rig-select'), rot = parseFloat(document.getElementById('framing-rotation')?.value || '0') || 0; if (sel && sel.selectedIndex >= 0) { const opt = sel.options[sel.selectedIndex]; updateScreenFovOverlay(opt.dataset.fovw, opt.dataset.fovh, rot); } updateReadoutFromCenter(); frameRafId = requestAnimationFrame(tick); }; frameRafId = requestAnimationFrame(tick); }
    function stopFrameOverlayLoop() { if (frameRafId) { cancelAnimationFrame(frameRafId); frameRafId = null; } }
    /** The frame overlay on, the Result on the view centre, the loop running. */
    function showFrameOverlay() {
        if (fovLayer) fovLayer.removeAll();
        const sel = document.getElementById('framing-rig-select'), rot = parseFloat(document.getElementById('framing-rotation')?.value || '0') || 0;
        if (sel && sel.selectedIndex >= 0) { const opt = sel.options[sel.selectedIndex]; updateScreenFovOverlay(opt.dataset.fovw, opt.dataset.fovh, rot); }
        if (isFramingTabVisible()) startFrameOverlayLoop();
        updateReadoutFromCenter();
    }
    // Show a survey as the base layer, on the root the current mode gives it.
    // Always a fresh layer: Offline view switching relies on this to move the
    // base to the new root whatever is on screen.
    async function setSurvey(hipsId) {
        if (!aladin) return;
        // The layer to show: the requested survey, the default for an unknown ID, or NSNS
        // while CDS is unreachable. #survey-select and saved values keep the request.
        const choice = chooseDisplayedBase(hipsId);
        baseWanted = choice.survey;
        let newLayer;
        try {
            newLayer = createSurveyLayer('base', choice.survey, resolveHipsId(choice.survey));
        } catch (e) {
            console.error("Error creating external survey layer:", e);
            await novaAlert(window.t('survey_load_error'));
            return;
        }
        aladin.setBaseImageLayer(newLayer);
        if (choice.notice) showSurveyNotice();
        else hideSurveyNotice();
    }
    // Apply the image-adjustment sliders to one Aladin layer (any layer that
    // supports these methods).
    function applyImageAdjustments(layer) {
        if (!layer) return;
        const b = parseFloat(document.getElementById('img-bright').value);
        const c = parseFloat(document.getElementById('img-contrast').value);
        const g = parseFloat(document.getElementById('img-gamma').value);
        const s = parseFloat(document.getElementById('img-sat').value);
        // Check for the existence of each function before calling it
        if (typeof layer.setBrightness === 'function') layer.setBrightness(b);
        if (typeof layer.setContrast === 'function') layer.setContrast(c);
        if (typeof layer.setGamma === 'function') layer.setGamma(g);
        if (typeof layer.setSaturation === 'function') layer.setSaturation(s);
    }
    function updateImageAdjustments() {
        // 1. Apply settings to the main base survey layer
        if (aladin && typeof aladin.getBaseImageLayer === 'function') {
            applyImageAdjustments(aladin.getBaseImageLayer());
        }

        // 2. Apply the *same* settings to the blend overlay layer, if it exists
        if (aladin && typeof aladin.getOverlayImageLayer === 'function') {
            try {
                applyImageAdjustments(aladin.getOverlayImageLayer('blend'));
            } catch (e) {
                // This might fail if the layer hasn't been created, which is fine
            }
        }
    }
    function updateReadout(raDeg, decDeg) {
        // Convert the J2000 coordinates from Aladin to JNow for the UI display
        const jNow = convertJ2000ToJNow(raDeg, decDeg);
        document.getElementById('ra-readout').value = formatRA(jNow.ra);
        document.getElementById('dec-readout').value = formatDec(jNow.dec);
    }
    /** The frame centre: always the Aladin view centre (J2000). The frame is fixed in the
     *  middle of the view, so this is what the readout, Copy, Save, the URL and the CSV use. */
    function frameCenter() { const rc = aladin.getRaDec(); return { ra: rc[0], dec: rc[1] }; }
    function updateReadoutFromCenter() { const c = frameCenter(); updateReadout(c.ra, c.dec); updateResultDiffers(); }

    // ---- Result block: the saved framing's values, and whether the view differs from them ----
    let savedResult = null;   // { ra, dec, angle, raJ2000, decJ2000 } of the saved framing, or null

    /** Saved values from an /api/get_framing response ("found"), or null. */
    function savedResultFrom(data) {
        if (!data || data.status !== 'found') return null;
        const ra = parseFloat(data.ra), dec = parseFloat(data.dec), rot = parseFloat(data.rotation);
        if (!Number.isFinite(ra) || !Number.isFinite(dec)) return null;
        const jNow = convertJ2000ToJNow(ra, dec);
        return {
            ra: formatRA(jNow.ra), dec: formatDec(jNow.dec),
            angle: `${Math.round(Number.isFinite(rot) ? rot : 0)}°`,
            // compared in J2000, so the slow drift of JNow never reads as a change
            raJ2000: formatRA(ra), decJ2000: formatDec(dec),
        };
    }

    /** Keep the saved framing for the Result block; before Aladin is live, show its values. */
    function setSavedFraming(data) {
        savedResult = savedResultFrom(data);
        if (!aladin && savedResult) {
            document.getElementById('ra-readout').value = savedResult.ra;
            document.getElementById('dec-readout').value = savedResult.dec;
            const angleEl = document.getElementById('result-angle');
            if (angleEl) angleEl.value = savedResult.angle;
        }
        updateResultDiffers();
    }

    /** "Saved framing" while the view matches the saved one, "Differs from saved" (with the
     *  saved values underneath) once it doesn't. Hidden when nothing is saved. */
    function updateResultDiffers() {
        const chip = document.getElementById('result-saved-chip');
        const line = document.getElementById('result-saved-line');
        if (!chip) return;
        if (!savedResult) { chip.hidden = true; if (line) line.hidden = true; return; }
        let differs = false;
        if (aladin) {
            const c = frameCenter();
            const angle = document.getElementById('result-angle')?.value;
            differs = formatRA(c.ra) !== savedResult.raJ2000 || formatDec(c.dec) !== savedResult.decJ2000
                || (angle !== undefined && angle !== savedResult.angle);
        }
        chip.hidden = false;
        chip.textContent = differs ? window.t('framing_differs_from_saved') : window.t('framing_saved_chip');
        chip.classList.toggle('is-different', differs);
        if (line) {
            line.hidden = !differs;
            if (differs) line.textContent = window.t('framing_saved_values', { ra: savedResult.ra, dec: savedResult.dec, angle: savedResult.angle });
        }
    }

    function copyToClipboard(text, onSuccess, onError) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            navigator.clipboard.writeText(text).then(onSuccess).catch(function(err) {
                console.error('Clipboard write failed:', err);
                if (onError) onError(err);
            });
            return;
        }
        // Fallback for non-secure contexts (navigator.clipboard is undefined)
        try {
            const ta = document.createElement('textarea');
            ta.value = text;
            ta.style.position = 'fixed';
            ta.style.opacity = '0';
            document.body.appendChild(ta);
            ta.focus();
            ta.select();
            const ok = document.execCommand('copy');
            document.body.removeChild(ta);
            if (ok) {
                if (onSuccess) onSuccess();
            } else {
                throw new Error('execCommand copy returned false');
            }
        } catch (err) {
            console.error('Clipboard fallback failed:', err);
            if (onError) onError(err);
        }
    }
    function copyRaDec() {
        const text = `${document.getElementById('ra-readout').value} ${document.getElementById('dec-readout').value}`;
        copyToClipboard(text);
    }
    /** Result block Copy buttons: 'ra', 'dec', 'angle' (the number only, for the ASIAIR
     *  field) or 'all' (three lines). Same values as the readout and the rotation badge. */
    function copyResult(kind) {
        const ra = document.getElementById('ra-readout').value, dec = document.getElementById('dec-readout').value;
        const angle = document.getElementById('result-angle').value;
        const angleNum = angle.replace(/°$/, '');
        const text = kind === 'ra' ? ra : kind === 'dec' ? dec : kind === 'angle' ? angleNum
            : `RA ${ra}\nDec ${dec}\n${window.t('framing_angle')} ${angle}`;
        copyToClipboard(text);
    }
    function changeView(view, skipPhaseUpdate = false) {
        // Sync the active state of the segmented view buttons (CSS targets [aria-pressed="true"])
        document.querySelectorAll('.date-controls .view-buttons .view-button').forEach(btn => {
            btn.setAttribute('aria-pressed', btn.dataset.view === view ? 'true' : 'false');
        });

        const day = document.getElementById('day-select').value, month = document.getElementById('month-select').value, year = document.getElementById('year-select').value, objectName = NOVA_GRAPH_DATA.objectName;
        fetch(`/get_date_info/${encodeURIComponent(objectName)}?day=${day}&month=${month}&year=${year}&location=${encodeURIComponent(NOVA_GRAPH_DATA.plotLocName || '')}`)
            .then(response => response.json())
            .then(data => {
                if (!skipPhaseUpdate) {
                    document.getElementById("phase-display").innerText = data.phase + "%";
                }
                document.getElementById("dusk-display").innerText = data.astronomical_dusk;
                document.getElementById("dawn-display").innerText = data.astronomical_dawn;
                if (data.date_display) document.getElementById("date-display").innerText = data.date_display;
            });

        // Handle secondary object controls visibility (only show in day view)
        const secondaryControls = document.getElementById('secondary-object-controls');
        if (secondaryControls) {
            if (view === 'day') {
                secondaryControls.style.display = 'inline-flex';
                // Restore title if we have a secondary object selected
                if (secondaryObjectName && secondaryObjectDisplayName) {
                    const primaryName = NOVA_GRAPH_DATA.altName || NOVA_GRAPH_DATA.objectName;
                    const primaryId = NOVA_GRAPH_DATA.objectName;
                    const titlePrimary = `${primaryName} (${primaryId})`;
                    const titleSecondary = `${secondaryObjectDisplayName} (${secondaryObjectName})`;
                    document.title = `${titlePrimary} & ${titleSecondary} – Nova DSO Tracker`;
                } else {
                    // Primary only
                    const primaryName = NOVA_GRAPH_DATA.altName || NOVA_GRAPH_DATA.objectName;
                    const primaryId = NOVA_GRAPH_DATA.objectName;
                    document.title = `${primaryName} (${primaryId}) – Nova DSO Tracker`;
                }
            } else {
                secondaryControls.style.display = 'none';
                // Clear secondary when switching to month/year view
                removeSecondaryDataset();
                document.title = 'Graph – Nova DSO Tracker';
            }
        }

        if (view === 'day') renderClientSideChart();
        else renderMonthlyYearlyChart(view);
    }
    function resetFovCenterToObject() { updateFramingChart(true); updateFovVsObjectLabel(); }
    function updateFovVsObjectLabel() { const el = document.getElementById('fov-vs-object'); if (!el) return; const sel = document.getElementById('framing-rig-select'); if (!sel || sel.selectedIndex < 0) { el.textContent = ''; return; } const opt = sel.options[sel.selectedIndex]; const fovW = parseFloat(opt.dataset.fovw), fovH = parseFloat(opt.dataset.fovh); if (!isFinite(fovW) || !isFinite(fovH)) { el.textContent = ''; return; } el.textContent = window.t('framing_fov_chip', { w: Math.round(fovW), h: Math.round(fovH) }); }
    function pad(n, w = 2) { return n.toString().padStart(w, '0'); }
    function formatRA(raDeg) { const totalSec = raDeg / 15 * 3600, h = Math.floor(totalSec / 3600), m = Math.floor((totalSec % 3600) / 60), s = (totalSec % 60).toFixed(2); return `${pad(h)}:${pad(m)}:${pad(s, 5)}`; }
    function formatDec(decDeg) { const sign = decDeg >= 0 ? '+' : '-', abs = Math.abs(decDeg), d = Math.floor(abs), m = Math.floor((abs - d) * 60), s = ((abs - d) * 60 - m) * 60; return `${sign}${pad(d)}:${pad(m)}:${s.toFixed(1).padStart(4, '0')}`; }

    // Project notes as last loaded or saved, so unsaved edits (typed, or put there by
    // Ask Nova, which does not save) can be told apart before leaving the page.
    let projectNotesBaseline = null;
    function captureProjectNotesBaseline() {
        const input = document.getElementById('project-field-hidden');
        if (input) projectNotesBaseline = input.value;
    }
    (function watchProjectNotesBaseline() {
        const editorEl = document.getElementById('project-field-editor');
        if (!editorEl) return;
        if (editorEl.editor) captureProjectNotesBaseline();
        else editorEl.addEventListener('trix-initialize', captureProjectNotesBaseline, { once: true });
    })();

    function hasUnsavedChanges() {
        if (document.querySelector('#journal-detail-form .form-unsaved-marker:not([hidden])')) return true;
        const input = document.getElementById('project-field-hidden');
        return !!input && projectNotesBaseline !== null && input.value !== projectNotesBaseline;
    }

    /** The option the page was rendered with: the page's location. */
    function pageLocationOption(select) {
        return Array.from(select.options).find(o => o.defaultSelected);
    }

    /**
     * #location-select changed: the dashboard's location switch, then this page for the
     * new location. Saved as the user's selected location (a guest's save is refused;
     * the page still follows ?location=) and in sessionStorage for the dashboard in this
     * tab. A reload, not an in-place update: the chart's time zone is fixed at load.
     */
    async function setLocation(select) {
        const name = select.value;
        select.disabled = true;   // no second change while this one runs
        if (hasUnsavedChanges()) {
            const i18n = typeof graphI18n !== 'undefined' ? graphI18n : {};
            const leave = await novaConfirm(i18n.unsavedChanges, {
                confirmLabel: i18n.discard, cancelLabel: i18n.cancel, danger: true
            });
            if (!leave) {
                const previous = pageLocationOption(select);
                if (previous) select.value = previous.value;
                select.disabled = false;
                return;
            }
        }
        try {
            sessionStorage.setItem('selectedLocation', name);
        } catch (e) {
            console.warn('Could not store the location for the dashboard:', e);
        }
        try {
            const response = await fetch('/set_location', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({location: name})
            });
            const data = await response.json();   // a guest is redirected to the login page: not JSON
            if (data.status !== 'success') console.warn('Failed to persist location:', data.message);
        } catch (error) {
            console.warn('Failed to persist location:', error);
        }
        // Not location.reload(): a ?location= already in the URL would win over the saved one.
        const url = new URL(window.location.href);
        url.searchParams.set('location', name);
        window.location.href = url.toString();
    }

    // Back to this page from the next one (bfcache): the select shows the page's location again.
    window.addEventListener('pageshow', (e) => {
        if (!e.persisted) return;
        const select = document.getElementById('location-select');
        if (!select) return;
        const current = pageLocationOption(select);
        if (current) select.value = current.value;
        select.disabled = false;
    });
    function saveProject() {
        // --- MODIFIED ---
        // We get the HTML content from the hidden input field, which Trix keeps in sync.
        const newProject = document.getElementById('project-field-hidden').value;
        // --- END MODIFICATION ---
    
        const objectName = NOVA_GRAPH_DATA.objectName;
        fetch('/update_project', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({object: objectName, project: newProject})
        })
        .then(res => res.json())
        .then(async data => {
            if (data.status === "success") projectNotesBaseline = newProject;
            await novaAlert(data.status === "success" ? "Project updated successfully!" : data.error);
        });
    }
    
        function buildFramingQuery() { const sel = document.getElementById('framing-rig-select'), rig = sel && sel.selectedIndex >= 0 ? sel.options[sel.selectedIndex].value : '', rigUid = sel && sel.selectedIndex >= 0 ? sel.options[sel.selectedIndex].dataset.rigUid : '', rotInput = document.getElementById('framing-rotation'), rot = rotInput ? (parseFloat(rotInput.value) || 0) : 0, sSel = document.getElementById('survey-select'), survey = sSel ? sSel.value : '', bSel = document.getElementById('blend-survey-select'), bOp = document.getElementById('blend-opacity'), blend = bSel ? bSel.value : '', blend_op = bOp ? (parseFloat(bOp.value) || 0) : 0; const { ra, dec } = aladin ? frameCenter() : { ra: NaN, dec: NaN };
            const cols = document.getElementById('mosaic-cols')?.value || 1;
        const rows = document.getElementById('mosaic-rows')?.value || 1;
        const overlap = document.getElementById('mosaic-overlap')?.value || 10;
        // Image adjustment values
        const imgBright = parseFloat(document.getElementById('img-bright')?.value || 0);
        const imgContrast = parseFloat(document.getElementById('img-contrast')?.value || 0);
        const imgGamma = parseFloat(document.getElementById('img-gamma')?.value || 1);
        const imgSat = parseFloat(document.getElementById('img-sat')?.value || 0);
        const qp = new URLSearchParams();
        if (rig) qp.set('rig', rig);
        if (rigUid) qp.set('rig_uid', rigUid);
        if (Number.isFinite(ra)) qp.set('ra', ra.toFixed(6));
        if (Number.isFinite(dec)) qp.set('dec', dec.toFixed(6));
        const utils = fu();
        qp.set('rot', String(Math.round(utils.normalizeAngle ? utils.normalizeAngle(rot) : to360(rot))));
        if (survey) qp.set('survey', survey);
        if (blend) qp.set('blend', blend);
        qp.set('blend_op', String(Math.max(0, Math.min(1, blend_op))));

        // Mosaic Params
        if (cols > 1 || rows > 1) {
            qp.set('m_cols', cols);
            qp.set('m_rows', rows);
            qp.set('m_ov', overlap);
        }

        // Image Adjustment Params (only if non-default)
        if (imgBright !== 0) qp.set('img_b', imgBright.toFixed(2));
        if (imgContrast !== 0) qp.set('img_c', imgContrast.toFixed(2));
        if (imgGamma !== 1) qp.set('img_g', imgGamma.toFixed(2));
        if (imgSat !== 0) qp.set('img_s', imgSat.toFixed(2));

        return '?' + qp.toString();
    }

    function copyFramingUrl() {
        // tab=framing goes last, so the framing parameters before it read as they always did
        const q = buildFramingQuery() + '&tab=framing', url = location.origin + location.pathname + q;
        copyToClipboard(url, function() {
            console.log("[Framing] Copied URL:", url);
        }, function(err) {
            console.warn("[Framing] copyFramingUrl failed:", err);
        });
    }
    function openInStellarium() {
        const status = document.getElementById('stellarium-status');
        const setStatus = (text, cls) => { status.textContent = text; status.className = 'stellarium-status' + (cls ? ' ' + cls : ''); };
        setStatus(window.t('stellarium_sending'), '');
        const objectName = NOVA_GRAPH_DATA.objectName;
        fetch("/proxy_focus", { method: "POST", headers: {"Content-Type": "application/x-www-form-urlencoded"}, body: new URLSearchParams({target: objectName, mode: "center"}) })
            .then(async response => {
                let data;
                try { data = await response.json(); } catch (e) { data = {message: window.t('stellarium_bad_response')}; }
                if (response.ok && data.status === "success") setStatus(window.t('stellarium_updated'), 'is-ok');
                else setStatus(window.t('stellarium_error', { message: data.message || window.t('stellarium_unknown_error') }), 'is-error');
            });
    }
    function startCurrentTimeUpdater(chartInstance) {
        if (!chartInstance) return;
        if (currentTimeUpdateInterval) clearInterval(currentTimeUpdateInterval);
    
        const updateLine = () => {
            if (!chartInstance || !chartInstance.options || !chartInstance.options.plugins?.annotation?.annotations) {
                if (currentTimeUpdateInterval) clearInterval(currentTimeUpdateInterval);
                currentTimeUpdateInterval = null;
                return;
            }
            const nowMs = luxon.DateTime.now().setZone(plotTz).toMillis();
            const annotations = chartInstance.options.plugins.annotation.annotations;
            if (annotations.currentTimeLine) {
                annotations.currentTimeLine.xMin = nowMs;
                annotations.currentTimeLine.xMax = nowMs;
                chartInstance.update('none');
            }
        };
        updateLine();
        currentTimeUpdateInterval = setInterval(updateLine, 60000);
    }
    
    function saveFramingToDB() {
        const objectName = NOVA_GRAPH_DATA.objectName;
        const sel = document.getElementById('framing-rig-select');

        // 1. Gather Data
        const center = frameCenter();
        const payload = {
            object_name: objectName,
            rig: sel?.value || '',
            rotation: parseFloat(document.getElementById('framing-rotation')?.value || 0),
            survey: document.getElementById('survey-select')?.value || '',
            blend: document.getElementById('blend-survey-select')?.value || '',
            blend_op: parseFloat(document.getElementById('blend-opacity')?.value || 0),
            ra: center.ra,
            dec: center.dec,
            // Mosaic Fields
            mosaic_cols: parseInt(document.getElementById('mosaic-cols')?.value || 1),
            mosaic_rows: parseInt(document.getElementById('mosaic-rows')?.value || 1),
            mosaic_overlap: parseFloat(document.getElementById('mosaic-overlap')?.value || 10),
            // Image Adjustment Fields
            img_brightness: parseFloat(document.getElementById('img-bright')?.value || 0),
            img_contrast: parseFloat(document.getElementById('img-contrast')?.value || 0),
            img_gamma: parseFloat(document.getElementById('img-gamma')?.value || 1),
            img_saturation: parseFloat(document.getElementById('img-sat')?.value || 0),
            // Overlay Preferences
            geo_belt_enabled: document.getElementById('show-geo-belt')?.checked ?? true
        };

        // Debug: Log payload being saved
        // 2. Send to DB
        fetch('/api/save_framing', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        })
        .then(r => r.json())
        .then(async data => {
            if(data.status === 'success') {
                await novaAlert(window.t('framing_saved'));
                checkAndShowFramingButton();
            } else {
                await novaAlert(`${window.t('error_saving')}: ${data.message}`);
            }
        });
    }
    
    function formatRaAsiair(raDeg) {
        const totalSec = raDeg / 15 * 3600;
        const h = Math.floor(totalSec / 3600);
        const m = Math.floor((totalSec % 3600) / 60);
        const s = (totalSec % 60).toFixed(2);
        return `${h}h ${m}m ${s}s`;
    }
    
    function formatDecAsiair(decDeg) {
        const sign = decDeg >= 0 ? '+' : '-';
        const abs = Math.abs(decDeg);
        const d = Math.floor(abs);
        const m = Math.floor((abs - d) * 60);
        const s = ((abs - d) * 60 - m) * 60;
        return `${sign}${d}° ${m}' ${s.toFixed(2)}"`;
    }
    
    // CSV Formatters (Exact Telescopius Match)
    function formatRaCsv(raDeg) {
        const totalSec = raDeg / 15 * 3600;
        const h = Math.floor(totalSec / 3600);
        const m = Math.floor((totalSec % 3600) / 60);
        const s = Math.round(totalSec % 60); // Integer seconds to match "52""
        // Format: 00hr 47' 52"
        return `${pad(h)}hr ${pad(m)}' ${pad(s)}"`;
    }
    
    function formatDecCsv(decDeg) {
        const abs = Math.abs(decDeg);
        const d = Math.floor(abs);
        const m = Math.floor((abs - d) * 60);
        const s = Math.round(((abs - d) * 60 - m) * 60);
    
        // Telescopius Format: 41º 53' 27" (No '+' for positive, uses º ordinal)
        const signStr = decDeg < 0 ? '-' : '';
        // Fix: Use pad(d) to ensure degrees are 2 digits (e.g. 06º instead of 6º)
        return `${signStr}${pad(d)}º ${pad(m)}' ${pad(s)}"`;
    }
    
    async function copyAsiairMosaic() {
        if (!aladin) return;

        const cols = parseInt(document.getElementById('mosaic-cols')?.value || 1);
        const rows = parseInt(document.getElementById('mosaic-rows')?.value || 1);
        const overlapPercent = parseFloat(document.getElementById('mosaic-overlap')?.value || 0);
        const overlap = overlapPercent / 100;
        const rotDeg = parseFloat(document.getElementById('framing-rotation')?.value || 0);

        const fovRigSel = document.getElementById('framing-rig-select');
        if (!fovRigSel || fovRigSel.selectedIndex < 0) {
            await novaAlert(window.t('please_select_rig'));
            return;
        }
        const opt = fovRigSel.options[fovRigSel.selectedIndex];
        // Keep raw arcmin for CSV output
        const fovWArcmin = parseFloat(opt.dataset.fovw);
        const fovHArcmin = parseFloat(opt.dataset.fovh);

        const fovW = fovWArcmin / 60; // degrees
        const fovH = fovHArcmin / 60; // degrees

        const wStep = fovW * (1 - overlap);
        const hStep = fovH * (1 - overlap);

        const center = frameCenter();
        const raCenterDeg = center.ra;
        const decCenterDeg = center.dec;

        // Rotation angle for Position Angle (inverted for CSS vs math convention)
        const ang = -rotDeg * Math.PI / 180;

        let clipboardText = "";
        // Exact Telescopius Header
        clipboardText += "Pane, RA, DEC, Position Angle (East), Pane width (arcmins), Pane height (arcmins), Overlap, Row, Column\n";

        // Prepare Base Name
        const rotInt = Math.round((rotDeg % 360 + 360) % 360);
        const safeName = (NOVA_GRAPH_DATA.objectName || "Target").replace(/\s+/g, '_');

        let paneCount = 1;

        for (let r = 0; r < rows; r++) {
            for (let c = 0; c < cols; c++) {
                // Calculate unrotated offset from mosaic center (in degrees)
                // Grid indexed from center: c=0 is left, c=cols-1 is right
                const cx_off = (c - (cols - 1) / 2.0) * wStep;
                // Grid indexed from center: r=0 is bottom, r=rows-1 is top
                const cy_off = (r - (rows - 1) / 2.0) * hStep;

                // Apply 2D rotation matrix for Position Angle
                // This rotates the grid around the center
                const rx = cx_off * Math.cos(ang) - cy_off * Math.sin(ang);
                const ry = cx_off * Math.sin(ang) + cy_off * Math.cos(ang);

                // SPHERICAL STEPPING ALGORITHM (N.I.N.A. / ASIAIR compatible)
                // Step 1: Calculate declination using spherical approximation
                // Dec offset is simply the rotated Y offset
                const paneDecDeg = decCenterDeg + ry;

                // Step 2: Calculate RA offset with cosine correction at the panel's declination
                // RA spacing varies with 1/cos(Dec) due to converging meridians
                // Safety: avoid division by zero near poles (|Dec| > 89.9°)
                let cosDec;
                if (Math.abs(paneDecDeg) > 89.9) {
                    cosDec = 0.00175; // Minimum value for ~89.9°
                } else {
                    cosDec = Math.cos(paneDecDeg * Math.PI / 180);
                }
                const raOffsetDeg = rx / cosDec;

                // Step 3: Apply RA offset to center RA
                let paneRaDeg = raCenterDeg + raOffsetDeg;

                // Normalize RA to [0, 360) range
                paneRaDeg = ((paneRaDeg % 360) + 360) % 360;

                // Full Row Construction (Match spacing)
                const pName = `${safeName}_Rot${rotInt}_P${paneCount}`;
                const pRa = formatRaCsv(paneRaDeg);
                const pDec = formatDecCsv(paneDecDeg);
                const pRot = rotInt.toFixed(2);
                const pW = fovWArcmin.toFixed(2);
                const pH = fovHArcmin.toFixed(2);
                const pOv = `${Math.round(overlapPercent)}%`; // Telescopius uses 15% (int)
                const pRow = r + 1;
                const pCol = c + 1;

                clipboardText += `${pName}, ${pRa}, ${pDec}, ${pRot}, ${pW}, ${pH}, ${pOv}, ${pRow}, ${pCol}\n`;
                paneCount++;
            }
        }
    
        copyToClipboard(clipboardText, async function() {
            await novaAlert(
                `Copied ${paneCount-1} pane(s) to clipboard (CSV Format).\n\n` +
                `• ASIAIR: Go to Plan > Import > Paste.\n` +
                `• N.I.N.A.: Save as .csv and import into Sequencer.\n\n` +
                `NOTE: Coordinates are J2000. Rotation is included.`
            );
        }, async function() {
            await novaAlert("Failed to copy to clipboard. See console.");
        });
    }

    async function deleteSavedFraming() {
        const objectName = NOVA_GRAPH_DATA.objectName;
        if (!(await novaConfirm(window.t('delete_saved_framing_confirm')))) return;

        fetch('/api/delete_framing', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ object_name: objectName })
        })
        .then(r => r.json())
        .then(async data => {
            if(data.status === 'success') {
                // Clear the saved framing buttons from the UI
                const container = document.getElementById('project-framing-buttons');
                if (container) {
                    const savedButtons = container.querySelectorAll('[data-saved-framing]');
                    savedButtons.forEach(btn => btn.remove());
                }
                setSavedFraming(null);
            } else {
                await novaAlert(window.t('delete_saved_framing_error', { message: data.message }));
            }
        });
    }
    
    function checkAndShowFramingButton() {
        const objectName = NOVA_GRAPH_DATA.objectName;
        const container = document.getElementById('project-framing-buttons');
        if (!container) return;

        fetch(`/api/get_framing/${encodeURIComponent(objectName)}`)
            .then(r => r.json())
            .then(data => {
                // Remove any existing saved framing buttons
                const existingSavedButtons = container.querySelectorAll('[data-saved-framing]');
                existingSavedButtons.forEach(btn => btn.remove());

                setSavedFraming(data);
                if (data.status === 'found') {
                    // 1. OPEN SAVED FRAMING BUTTON
                    const btn = document.createElement('button');
                    btn.type = 'button';
                    btn.className = 'inline-button-ghost';
                    btn.textContent = window.t('open_saved_framing');
                    btn.setAttribute('data-saved-framing', 'true');

                    btn.onclick = () => openFramingQueued(savedFramingQuery(data));

                    container.appendChild(btn);

                    // 2. DELETE SAVED FRAMING BUTTON
                    const delBtn = document.createElement('button');
                    delBtn.type = 'button';
                    delBtn.className = 'inline-button-danger';
                    delBtn.textContent = window.t('delete_saved_framing');
                    delBtn.title = window.t('delete_saved_framing');
                    delBtn.setAttribute('data-saved-framing', 'true');

                    delBtn.onclick = deleteSavedFraming;

                    container.appendChild(delBtn);
                }
            });
    }

    /** Query string for openFramingAssistant from an /api/get_framing "found" response. */
    function savedFramingQuery(data) {
        const params = new URLSearchParams();
        if(data.rig != null) params.set('rig', data.rig);
        if (data.rig_uid) params.set('rig_uid', data.rig_uid);
        if(data.ra != null) params.set('ra', data.ra);
        if(data.dec != null) params.set('dec', data.dec);
        if(data.rotation != null) params.set('rot', data.rotation);
        if(data.survey) params.set('survey', data.survey);
        if(data.blend) params.set('blend', data.blend);
        if(data.blend_op != null) params.set('blend_op', data.blend_op);

        // Restore Mosaic
        if(data.mosaic_cols != null) params.set('m_cols', data.mosaic_cols);
        if(data.mosaic_rows != null) params.set('m_rows', data.mosaic_rows);
        if(data.mosaic_overlap != null) params.set('m_ov', data.mosaic_overlap);

        // Restore Image Adjustments
        if(data.img_brightness != null && data.img_brightness !== 0) params.set('img_b', data.img_brightness);
        if(data.img_contrast != null && data.img_contrast !== 0) params.set('img_c', data.img_contrast);
        if(data.img_gamma != null && data.img_gamma !== 1) params.set('img_g', data.img_gamma);
        if(data.img_saturation != null && data.img_saturation !== 0) params.set('img_s', data.img_saturation);

        // Restore Overlay Preferences
        if(data.geo_belt_enabled != null) params.set('geo_belt', data.geo_belt_enabled ? '1' : '0');

        return params.toString();
    }
    
    window.addEventListener('load', () => {
        if (window['chartjs-plugin-annotation']) Chart.register(window['chartjs-plugin-annotation']);

        changeView('day', true);

        // Initialize secondary object comparison selector
        initSecondaryObjectSelector();

        // Global Trix File Upload Logic (Handles all editors on the page)
        document.addEventListener("trix-attachment-add", function(event) {
            if (event.attachment.file) {
                uploadTrixFile(event.attachment);
            }
        });
    
        function uploadTrixFile(attachment) {
            const formData = new FormData();
            formData.append("file", attachment.file);
    
            fetch("/upload_editor_image", { method: "POST", body: formData })
            .then(r => r.ok ? r.json() : Promise.reject(r))
            .then(data => {
                if(data.url) {
                    attachment.setAttributes({ url: data.url, href: data.url });
                }
            })
            .catch(async e => {
                console.error("Trix upload failed", e);
                await novaAlert("Image upload failed. See console for details.");
                attachment.remove();
            });
        }
    
        // The Framing tab may already be showing (?tab=framing, a framing URL, last tab)
        framingPageLoaded = true;
        if (isFramingTabVisible()) onFramingTabShown();

        // Date Picker Limits
        const dayInput = document.getElementById("day-select");
        if (dayInput) {
            const mSel = document.getElementById("month-select"), yInp = document.getElementById("year-select");
            const updateDL = () => {
                const d = new Date(parseInt(yInp.value), parseInt(mSel.value), 0).getDate();
                if(parseInt(dayInput.value) > d) dayInput.value = d;
                dayInput.max = d;
            };
            mSel.addEventListener("change", updateDL);
            yInp.addEventListener("change", updateDL);
            updateDL();
        }
    
        // Window resize handler for the Framing tab
        window.addEventListener('resize', () => {
            if (isFramingTabVisible()) {
                if (typeof aladin !== 'undefined' && aladin) updateFramingChart(false);
            }
        });

    });

    // Saved framing: the Open / Delete buttons, and its values in the Result block right
    // away (the DOM is parsed: this script is deferred), not only once the page has loaded
    checkAndShowFramingButton();

    const MAX_SCAN_FOV_DEG = 4.0;

    /** Show why the Scan Frame button cannot be used right now, keeping the button
     *  reachable rather than switching it off.
     *
     *  `aria-disabled` + a class instead of `disabled`, so the control stays in the
     *  tab order and keeps its tooltip: a `disabled` button cannot be focused
     *  (measured in Chromium and WebKit -- `el.focus()` leaves activeElement
     *  unchanged), which puts the explanation out of reach of the keyboard. The
     *  click is refused in scanFrameForDSOs instead of by the browser.
     *
     *  Both states go through here so neither can leave the other's mark behind.
     */
    function setScanButtonBlocked(btn, reason) {
        if (!btn) return;
        // Never `disabled`: the reason is carried by the class and the attribute
        // so the button stays focusable and hoverable. scanFrameForDSOs refuses
        // the click.
        btn.disabled = false;
        if (reason) {
            btn.setAttribute('aria-disabled', 'true');
            btn.classList.add('is-blocked');
            btn.title = reason;
        } else {
            btn.removeAttribute('aria-disabled');
            btn.classList.remove('is-blocked');
            btn.title = '';
        }
    }

    function updateScanButtonState() {
        const btn = document.getElementById('scan-frame-btn');
        if (!btn) return;
        if (offlineViewOn) {
            setScanButtonBlocked(btn, window.t('offline_scan_disabled'));
            return;
        }
        const sel = document.getElementById('framing-rig-select');
        const opt = sel ? sel.options[sel.selectedIndex] : null;
        if (!opt) { setScanButtonBlocked(btn, null); return; }
        const fovWDeg = parseFloat(opt.dataset.fovw) / 60;
        const fovHDeg = parseFloat(opt.dataset.fovh) / 60;
        if (fovWDeg > MAX_SCAN_FOV_DEG || fovHDeg > MAX_SCAN_FOV_DEG) {
            setScanButtonBlocked(btn, window.t('scan_fov_too_large', { w: fovWDeg.toFixed(1), h: fovHDeg.toFixed(1), max: MAX_SCAN_FOV_DEG }));
        } else {
            setScanButtonBlocked(btn, null);
        }
    }

    function applyMinSizeFilter(minSize) {
        if (!scanFrameActive || !scanResultsCatalog
                || !scanResultsAll.length) return;
        const popup = document.getElementById('scan-result-popup');
        if (popup) popup.hidden = true;
        const filtered = scanResultsAll.filter(obj =>
            minSize === 0 || (obj.size_arcmin !== null && obj.size_arcmin >= minSize)
        );
        scanResultsCatalog.removeAll();
        scanResultsCatalog.addSources(
            filtered.map(obj =>
                A.source(obj.ra, obj.dec, {
                    name:        obj.name,
                    otype:       obj.otype,
                    mag:         obj.mag,
                    size_arcmin: obj.size_arcmin,
                })
            )
        );
    }

    const SIMBAD_OTYPE_LABELS = {
        'G':   'Galaxy',            'GiG': 'Galaxy in Group',
        'GiC': 'Galaxy in Cluster', 'GlC': 'Globular Cluster',
        'OpC': 'Open Cluster',      'PN':  'Planetary Nebula',
        'SNR': 'SNR',               'HII': 'HII Region',
        'EmN': 'Emission Nebula',   'RfN': 'Reflection Nebula',
        'MoC': 'Molecular Cloud',   'Cl*': 'Star Cluster',
    };

    function scanFrameForDSOs() {
        const btn = document.getElementById('scan-frame-btn');
        const popup = document.getElementById('scan-result-popup');

        // A blocked button is not a disabled one any more (see setScanButtonBlocked),
        // so the click arrives here and is refused where the reason is known.
        if (btn && btn.getAttribute('aria-disabled') === 'true') return;

        if (scanFrameActive && scanResultsCatalog) {
            scanResultsCatalog.removeAll();
            scanFrameActive = false;
            scanResultsAll = [];
            const _si = document.getElementById('scan-min-size');
            if (_si) _si.disabled = true;
            if (btn) { btn.textContent = window.t('scan_frame'); setScanButtonBlocked(btn, null); }
            if (popup) popup.hidden = true;
            return;
        }

        if (!aladin) return;

        const center = frameCenter();
        const sel = document.getElementById('framing-rig-select');
        const opt = sel ? sel.options[sel.selectedIndex] : null;
        const fovWDeg = opt ? parseFloat(opt.dataset.fovw) / 60 : 1.0;
        const fovHDeg = opt ? parseFloat(opt.dataset.fovh) / 60 : 1.0;

        if (btn) {
            btn.textContent = window.t('scan_scanning');
            // Busy is a real disabled state: the button is not clickable and the
            // per-rig tooltip goes away while it runs.
            setScanButtonBlocked(btn, null);
            btn.disabled = true;
        }

        fetch('/api/scan_frame', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                ra:    center.ra,
                dec:   center.dec,
                fov_w: fovWDeg,
                fov_h: fovHDeg,
            }),
        })
        .then(r => r.json())
        .then(data => {
            if (btn) { btn.textContent = window.t('scan_frame'); setScanButtonBlocked(btn, null); }
            if (data.status !== 'success') {
                if (data.code === 'simbad_unavailable') {
                    novaAlert(window.t('simbad_unavailable'));
                }
                console.warn('[SCAN] SIMBAD error:', data.message);
                return;
            }
            if (!data.objects.length) {
                console.log('[SCAN] No DSOs found in frame.');
                return;
            }

            scanResultsAll = data.objects;
            const _sizeInput = document.getElementById('scan-min-size');
            const _minSize   = parseFloat(_sizeInput?.value) || 0.5;
            if (_sizeInput) _sizeInput.disabled = false;

            scanResultsCatalog = A.catalog({
                name:         'SIMBAD DSOs',
                shape:        'circle',
                sourceSize:   10,
                color:        '#e8a84c',
                labelColumn:  'name',
                displayLabel: true,
                labelColor:   '#e8a84c',
                labelFont:    '13px DM Sans, sans-serif',
                labelHalo:    true,
                labelHaloColor: '#000000',
                onClick: function(source) {
                    const nameEl = document.getElementById('scan-popup-name');
                    const bodyEl = document.getElementById('scan-popup-body');
                    if (!popup || !nameEl || !bodyEl) return;

                    // Position near clicked object
                    // The popup sits in .framing-sky, which the view fills: view pixels are popup pixels
                    const pix = aladin.world2pix(source.ra, source.dec);
                    const aladinDiv = document.getElementById('aladin-lite-div');
                    if (pix && aladinDiv) {
                        const px = pix[0], py = pix[1];
                        const pw = 230; // popup estimated width
                        const ph = 140; // popup estimated height
                        const mw = aladinDiv.offsetWidth;
                        const left = (px + 28 + pw < mw) ? px + 28 : px - pw - 8;
                        const top  = (py - ph > 20) ? py - ph : py + 16;
                        popup.style.setProperty('--popup-x', Math.max(8, left) + 'px');
                        popup.style.setProperty('--popup-y', Math.max(8, top) + 'px');
                        popup.classList.add('is-placed');
                    }

                    // Content
                    nameEl.textContent = source.data.name || '—';
                    const typeName = SIMBAD_OTYPE_LABELS[source.data.otype]
                                     || source.data.otype || '—';
                    const raHrs = source.ra / 15;
                    const raH   = Math.floor(raHrs);
                    const raM   = Math.floor((raHrs - raH) * 60);
                    const raS   = ((raHrs - raH) * 60 - raM) * 60;
                    const raStr = raH + 'h\u202f' + String(raM).padStart(2,'0')
                        + 'm\u202f' + raS.toFixed(1) + 's';
                    const dSign = source.dec >= 0 ? '+' : '−';
                    const dAbs  = Math.abs(source.dec);
                    const dD    = Math.floor(dAbs);
                    const dM    = Math.floor((dAbs - dD) * 60);
                    const dS    = ((dAbs - dD) * 60 - dM) * 60;
                    const decStr = dSign + dD + '°\u202f' + String(dM).padStart(2,'0')
                        + '′\u202f' + dS.toFixed(0) + '″';

                    let html = '<span class="scan-popup-type">' + window.escapeHtml(typeName) + '</span>'
                        + '<span class="scan-popup-coords">' + raStr + '<br>' + decStr + '</span>';
                    if (source.data.size_arcmin != null) {
                        html += '<span class="scan-popup-fact">'
                            + window.escapeHtml(window.t('scan_size')) + '\u2009' + source.data.size_arcmin.toFixed(1) + '</span>';
                    }
                    if (source.data.mag != null) {
                        html += '<span class="scan-popup-fact">'
                            + window.escapeHtml(window.t('scan_mag')) + '\u2009' + source.data.mag.toFixed(1) + '</span>';
                    }
                    bodyEl.innerHTML = html;

                    // "Add to DB" button
                    const _cfgUrl = new URL('/config_form', window.location.origin);
                    _cfgUrl.searchParams.set('prefill_id',   source.data.name || '');
                    _cfgUrl.searchParams.set('prefill_ra',   (source.ra / 15).toFixed(5));
                    _cfgUrl.searchParams.set('prefill_dec',  source.dec.toFixed(5));
                    _cfgUrl.searchParams.set('prefill_type', typeName);
                    _cfgUrl.searchParams.set('prefill_size',
                        source.data.size_arcmin != null
                            ? source.data.size_arcmin.toFixed(2) : '');
                    _cfgUrl.searchParams.set('prefill_config_tab', 'objects');
                    const _addBtn = document.createElement('a');
                    _addBtn.href      = _cfgUrl.toString();
                    _addBtn.target    = '_blank';
                    _addBtn.rel       = 'noopener';
                    _addBtn.addEventListener('click', function(e) {
                        e.preventDefault();
                        e.stopPropagation();
                        window.open(this.href, '_blank');
                    });
                    _addBtn.className = 'inline-button scan-popup-add';
                    _addBtn.textContent = window.t('scan_add_to_database');
                    bodyEl.appendChild(_addBtn);

                    // Wikipedia snippet
                    const _wikiName = (source.data.name || '')
                        .replace(/\s+/g, ' ').trim().replace(/ /g, '_');
                    const _wikiDiv = document.createElement('div');
                    _wikiDiv.className = 'scan-popup-wiki is-loading';
                    _wikiDiv.textContent = window.t('scan_loading');
                    bodyEl.appendChild(_wikiDiv);

                    fetch('https://en.wikipedia.org/api/rest_v1/page/summary/'
                          + encodeURIComponent(_wikiName))
                        .then(r => r.ok ? r.json() : null)
                        .then(d => {
                            if (!d || !d.extract) { _wikiDiv.remove(); return; }
                            const extract = d.extract.length > 220
                                ? d.extract.slice(0, 220) + '…' : d.extract;
                            _wikiDiv.classList.remove('is-loading');
                            _wikiDiv.innerHTML = '<span>' + window.escapeHtml(extract) + '</span> '
                                + '<a class="scan-popup-wiki-link" href="' + window.escapeHtml(d.content_urls.desktop.page) + '"'
                                + ' target="_blank" rel="noopener">Wikipedia →</a>';
                        })
                        .catch(() => _wikiDiv.remove());

                    popup.hidden = false;
                },
            });

            aladin.addCatalog(scanResultsCatalog);
            scanFrameActive = true;
            applyMinSizeFilter(_minSize);
        })
        .catch(err => {
            console.error('[SCAN] fetch error:', err);
            if (btn) { btn.textContent = window.t('scan_frame'); setScanButtonBlocked(btn, null); }
        });
    }

    // Expose functions needed by event delegation in graph_view.js
    // NOTE: showTab, showProjectSubTab, toggleProjectSubTabEdit are defined in graph_view.js, not here
    window.changeView = changeView;
    window.saveProject = saveProject;
    window.openFramingAssistant = openFramingAssistant;
    window.novaFramingTab = { shown: onFramingTabShown, hidden: onFramingTabHidden };
    window.toggleGeoBelt = toggleGeoBelt;
    window.flipFraming90 = flipFraming90;
    window.copyFramingUrl = copyFramingUrl;
    window.saveFramingToDB = saveFramingToDB;
    window.updateFramingChart = updateFramingChart;
    window.updateFovVsObjectLabel = updateFovVsObjectLabel;
    window.onRotationInput = onRotationInput;
    window.setSurvey = setSurvey;
    window.showSurveyNotice = showSurveyNotice;
    window.updateImageAdjustments = updateImageAdjustments;
    window.copyRaDec = copyRaDec;
    window.copyResult = copyResult;
    window.resetFovCenterToObject = resetFovCenterToObject;
    window.copyAsiairMosaic = copyAsiairMosaic;
    window.scanFrameForDSOs = scanFrameForDSOs;
    window.updateScanButtonState = updateScanButtonState;
    window.applyMinSizeFilter = applyMinSizeFilter;
    window.setLocation = setLocation;
    window._fetchPlotInputs = fetchPlotInputs;   // graph_view.js: Imaging Opportunities
    window.openInStellarium = openInStellarium;

    // ============================================
    // THEME INTEGRATION
    // ============================================

    /**
     * Update chart options with theme-aware colors
     * Updates grid colors, axis colors, and re-renders chart
     */
    function updateChartForTheme() {
        if (!window.altitudeChart) return;

        const isDark = window.stylingUtils && window.stylingUtils.isDarkTheme
            ? window.stylingUtils.isDarkTheme()
            : false;

        // Theme-aware colors
        const gridColor = isDark ? 'rgba(150, 150, 150, 0.3)' : 'rgba(128, 128, 128, 0.5)';
        const textColor = isDark ? '#e0e0e0' : '#333';
        const tickColor = isDark ? '#b0b0b0' : '#666';

        // Annotation label colors
        const annotationLabelColor = isDark ? '#e0e0e0' : '#222';
        const annotationBgColor = isDark ? 'rgba(40,40,40,0.92)' : 'rgba(255,255,255,0.92)';
        const annotationBorderColor = isDark ? 'rgba(100,100,100,0.5)' : 'rgba(0,0,0,0.15)';

        // Update chart options with new colors
        if (window.altitudeChart.options.scales) {
            window.altitudeChart.options.scales.x.grid.color = gridColor;
            window.altitudeChart.options.scales.yAltitude.grid.color = gridColor;
            window.altitudeChart.options.scales.yAzimuth.grid.drawOnChartArea = false;

            // Update tick colors
            if (window.altitudeChart.options.scales.x.ticks) {
                window.altitudeChart.options.scales.x.ticks.color = tickColor;
            }
            if (window.altitudeChart.options.scales.yAltitude.ticks) {
                window.altitudeChart.options.scales.yAltitude.ticks.color = tickColor;
            }
            if (window.altitudeChart.options.scales.yAzimuth.ticks) {
                window.altitudeChart.options.scales.yAzimuth.ticks.color = tickColor;
            }

            // Update title colors
            if (window.altitudeChart.options.scales.x.title) {
                window.altitudeChart.options.scales.x.title.color = textColor;
            }
            if (window.altitudeChart.options.scales.yAltitude.title) {
                window.altitudeChart.options.scales.yAltitude.title.color = textColor;
            }
            if (window.altitudeChart.options.scales.yAzimuth.title) {
                window.altitudeChart.options.scales.yAzimuth.title.color = textColor;
            }
        }

        // Update annotation label colors
        if (window.altitudeChart.options.plugins && window.altitudeChart.options.plugins.annotation) {
            const annotations = window.altitudeChart.options.plugins.annotation.annotations;
            if (annotations) {
                // Only the sun-event lines are themed: they are the ones built from
                // labelConfig. The Now and transit labels carry their own red/crimson
                // colours on purpose, and repainting them makes the day chart's
                // appearance depend on whether this pass runs before or after the
                // chart is created.
                const themedLabels = ['sunsetLine', 'duskLine', 'dawnLine', 'sunriseLine'];
                Object.keys(annotations).forEach(key => {
                    if (themedLabels.includes(key) && annotations[key].label) {
                        annotations[key].label.color = annotationLabelColor;
                        annotations[key].label.backgroundColor = annotationBgColor;
                        annotations[key].label.borderColor = annotationBorderColor;
                    }
                });
            }
        }

        // Update dataset colors
        if (window.altitudeChart.data.datasets) {
            // Object altitude
            window.altitudeChart.data.datasets[0].borderColor = window.stylingUtils && window.stylingUtils.getChartLineColor
                ? window.stylingUtils.getChartLineColor(0)
                : '#36A2EB';

            // Moon altitude
            window.altitudeChart.data.datasets[1].borderColor = window.stylingUtils && window.stylingUtils.getChartLineColor
                ? window.stylingUtils.getChartLineColor(1)
                : '#FFC107';

            // Horizon mask
            window.altitudeChart.data.datasets[2].borderColor = window.stylingUtils && window.stylingUtils.getChartLineColor
                ? window.stylingUtils.getChartLineColor(2)
                : '#636e72';

            // Horizon line
            window.altitudeChart.data.datasets[3].borderColor = window.stylingUtils && window.stylingUtils.getColor
                ? window.stylingUtils.getColor('--text-primary', '#333')
                : '#333';

            // Object azimuth
            window.altitudeChart.data.datasets[4].borderColor = window.stylingUtils && window.stylingUtils.getChartLineColor
                ? window.stylingUtils.getChartLineColor(0)
                : '#36A2EB';

            // Moon azimuth
            window.altitudeChart.data.datasets[5].borderColor = window.stylingUtils && window.stylingUtils.getChartLineColor
                ? window.stylingUtils.getChartLineColor(1)
                : '#FFC107';
        }

        // Update chart with new options
        window.altitudeChart.update('none');
    }

    // Register theme change callback
    if (window.stylingUtils && window.stylingUtils.onThemeChange) {
        window.stylingUtils.onThemeChange(function(event) {
            console.log('[graph_view_chart.js] Theme changed to:', event.detail.theme);
            updateChartForTheme();
        });

        // Initial update when stylingUtils is available
        // Wait a bit for chart to be initialized
        setTimeout(updateChartForTheme, 100);
    }

    // ============================================
    // END THEME INTEGRATION
    // ============================================

    // ==========================================================================
    // OFFLINE SKY: the Offline view switch
    //
    // Two flags, and they are not the same thing: NOVA_OFFLINE_SKY says the
    // /hips/ proxy is serving this browser at all (the survey map and the local
    // roots depend on it), NOVA_OFFLINE_SKY_UI says this installation *shows*
    // the switch (OFFLINE_SKY_UI resolved against the user mode, and the store
    // on). The cache-only roots belong to the first; the switch and its
    // connectivity listeners to the second.
    // ==========================================================================
    function offlineEnabled() { return !!window.NOVA_OFFLINE_SKY; }
    function offlineUiEnabled() { return !!window.NOVA_OFFLINE_SKY_UI; }
    function offlineBaseId() { return document.getElementById('survey-select')?.value || 'P/DSS2/color'; }

    function setOfflineView(on, fromUser) {
        offlineViewOn = !!on;
        if (fromUser) offlineViewUserSetting = offlineViewOn;
        const t = document.getElementById('offline-view-toggle');
        if (t) t.checked = offlineViewOn;
        applyOfflineViewToLayers();
        updateScanButtonState();
    }

    // Both layers are rebuilt on the new root, whatever they show now: no
    // checks, no early returns. The blend follows even when it is not visible (a
    // layer at opacity 0 still fetches its tiles): an invisible one is taken off
    // and built again on the right root once it is raised.
    function applyOfflineViewToLayers() {
        if (!aladin) return;
        setSurvey(offlineBaseId()).catch(() => {});
        const op = Number(document.getElementById('blend-opacity')?.value || 0);
        if (op > 0) {
            latestBlend = null;   // forces the rebuild; the old one is dropped if it lands late
            ensureBlendLayer();
            setBlendOpacity(op);
        } else {
            removeBlendLayer();
        }
        updateSurveyCredit();
    }

    // Aladin's own fallback: when its view has never shown a layer and the last one
    // failed, it sets alaskybis DSS2 as the base, by URL string. In Offline view that
    // would go to the network, so a string base layer is refused there. Nova's own
    // swaps always pass layer objects, so only that fallback is affected.
    function guardAladinFallback(al) {
        if (!al || al.__novaFallbackGuard) return;
        const original = al.setBaseImageLayer;
        al.setBaseImageLayer = function (layer) {
            if (typeof layer === 'string' && offlineViewOn) {
                console.warn('[FRAMING] Offline view: Aladin fallback not loaded:', layer);
                return null;
            }
            return original.apply(this, arguments);
        };
        al.__novaFallbackGuard = true;
    }

    let offlineStartChecked = false;
    function wireOfflineSkyUi() {
        // The switch and everything that switches it exist only where the
        // installation shows it. With the UI off nothing is installed: no
        // checkbox listener, no offline/online listener, no page-opened-offline
        // start -- so a real internet loss leaves the layer on its online root,
        // showing what is on disk.
        if (!offlineUiEnabled()) return;

        // A page opened while the browser is already offline never gets an
        // 'offline' event: start in Offline view (once; the events take over).
        if (!offlineStartChecked) {
            offlineStartChecked = true;
            if (navigator.onLine === false && !offlineViewOn) setOfflineView(true, false);
        }

        const toggle = document.getElementById('offline-view-toggle');
        if (toggle && !toggle.__novaOfflineWired) {
            toggle.__novaOfflineWired = true;
            toggle.addEventListener('change', () => setOfflineView(toggle.checked, true));
        }

        // Auto-on when the browser drops offline; restore the user's own setting
        // when connectivity returns.
        if (!window.__novaOfflineListeners) {
            window.__novaOfflineListeners = true;
            window.addEventListener('offline', () => setOfflineView(true, false));
            window.addEventListener('online', () => setOfflineView(offlineViewUserSetting === true, false));
        }
    }

    // Expose theme update function for external calls
    window.updateChartForTheme = updateChartForTheme;

    // Offline view from page load: the switch, the browser's offline/online events,
    // and a page opened offline -- before the Framing tab is first shown.
    wireOfflineSkyUi();

})();

