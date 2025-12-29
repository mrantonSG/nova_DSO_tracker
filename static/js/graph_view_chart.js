
let currentTimeUpdateInterval = null;

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

function convertJNowToJ2000(raDeg, decDeg) {
    const jdNow = (new Date().getTime() / 86400000.0) + 2440587.5;
    const m = getPrecessionMatrix(jdNow);
    // Transpose matrix for inverse rotation
    const mt = [ [m[0][0], m[1][0], m[2][0]], [m[0][1], m[1][1], m[2][1]], [m[0][2], m[1][2], m[2][2]] ];
    const raRad = raDeg * Math.PI / 180.0, decRad = decDeg * Math.PI / 180.0;
    const x = Math.cos(decRad) * Math.cos(raRad);
    const y = Math.cos(decRad) * Math.sin(raRad);
    const z = Math.sin(decRad);
    const v2 = applyMatrix(mt, [x, y, z]);
    const r = Math.sqrt(v2[0]*v2[0] + v2[1]*v2[1]);
    let raJ2000 = Math.atan2(v2[1], v2[0]);
    if (raJ2000 < 0) raJ2000 += 2 * Math.PI;
    const decJ2000 = Math.atan2(v2[2], r);
    return { ra: raJ2000 * 180.0 / Math.PI, dec: decJ2000 * 180.0 / Math.PI };
}
// --- End Precession Helpers ---

const weatherOverlayPlugin = {
  id: 'weatherOverlay',
  beforeDatasetsDraw(chart) {
    const info = chart.__weather;
    // --- NEW: Handle loading and error states ---
    if (!info) return; // Info object not even created yet

    if (info.isLoading) {
        this.drawMessage(chart, 'Loading weather...');
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

    // --- UPDATED: Force 3-hour grouping ---
    const groupedForecast = [];
    if (originalForecast.length > 0) {
        originalForecast.sort((a, b) => a.start - b.start);

        // --- THIS IS THE FIX: We now ALWAYS group into 3-hour blocks ---
        const groupDurationMs = 3 * 3600 * 1000; // 3 hours in ms
        const groupIntervalHours = 3;
        // --- END FIX ---

        // Find the start hour of the very first block (0, 3, 6...)
        const firstBlockStartHour = new Date(originalForecast[0].start).getUTCHours();
        const firstGroupStartHour = Math.floor(firstBlockStartHour / groupIntervalHours) * groupIntervalHours;
        const baseTimeMs = new Date(originalForecast[0].start).setUTCHours(0,0,0,0);

        let currentGroupStart = baseTimeMs + firstGroupStartHour * 3600 * 1000;
        let groupEnd = currentGroupStart + groupDurationMs;
        let blocksInGroup = [];

        for (const block of originalForecast) {
            // If block starts at or after the current group ends, finalize the previous group
            if (block.start >= groupEnd) {
                if (blocksInGroup.length > 0) {
                    this.finalizeGroup(groupedForecast, blocksInGroup, currentGroupStart, groupEnd);
                }
                // Start a new group *at* the end of the last one
                currentGroupStart = groupEnd;
                groupEnd = currentGroupStart + groupDurationMs;
                blocksInGroup = [];

                // Handle gaps: keep advancing groups until we find the one this block belongs in
                while(block.start >= groupEnd) {
                    currentGroupStart = groupEnd;
                    groupEnd = currentGroupStart + groupDurationMs;
                }
            }
             // Add block to the current group if it starts within the time window
             if (block.start >= currentGroupStart && block.start < groupEnd) {
                blocksInGroup.push(block);
             }
        }
        // Add the last group
        if (blocksInGroup.length > 0) {
            this.finalizeGroup(groupedForecast, blocksInGroup, currentGroupStart, groupEnd);
        }
    }
    // --- END UPDATED GROUPING LOGIC ---

    const { ctx, chartArea, scales } = chart;
    const x = scales.x;
    if (!x || !chartArea) return;

    const rowH = 18; // px per row
    const gap = 2;   // px between rows
    const pad = 1;   // inner padding for blocks
    const numRows = hasValidSeeingData ? 2 : 1;
    const totalH = rowH * numRows + (hasValidSeeingData ? gap : 0);
    const topY = chartArea.top - (totalH + 6);

    ctx.save();
    ctx.globalAlpha = 1;
    ctx.shadowColor = 'transparent';
    ctx.font = '12px system-ui, Arial';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';

    const getTextColorForBackground = (rgbaColor) => {
        if (!rgbaColor || !rgbaColor.startsWith('rgba')) return '#333';
        try {
            const [r, g, b] = rgbaColor.match(/\d+/g).map(Number);
            const luminance = (0.299 * r + 0.587 * g + 0.114 * b);
            return luminance < 128 ? '#FFFFFF' : '#333';
        } catch (e) {
            return '#333';
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

    const drawBlock = (y, x0, x1, label, fill) => {
      const left = Math.round(Math.min(x0, x1));
      const width = Math.max(1, Math.round(Math.abs(x1 - x0)));
      if (width < 1) return; // Don't draw tiny blocks

      ctx.fillStyle = fill;
      ctx.fillRect(left + pad, y + pad, width - 2 * pad, rowH - 2 * pad);
      ctx.strokeStyle = 'rgba(0,0,0,0.1)';
      ctx.lineWidth = 1;
      ctx.strokeRect(left + pad + 0.5, y + pad + 0.5, width - 2 * pad - 1, rowH - 2 * pad - 1);

      const maxTextW = Math.max(0, width - 8);
      if (maxTextW < 5) return; // Don't draw text if too small

      const txt = fitText(label, maxTextW);
      ctx.fillStyle = getTextColorForBackground(fill);
      ctx.fillText(txt, left + width / 2, y + rowH / 2);
    };

    const xMinPx = x.getPixelForValue(x.min);
    const xMaxPx = x.getPixelForValue(x.max);

    groupedForecast.forEach(b => {
      let x0 = x.getPixelForValue(b.start);
      let x1 = x.getPixelForValue(b.end);
      if (!Number.isFinite(x0) || !Number.isFinite(x1)) return;
      x0 = Math.max(xMinPx, Math.min(x0, xMaxPx));
      x1 = Math.max(xMinPx, Math.min(x1, xMaxPx));
      if (Math.abs(x1 - x0) < 1) return;

      const ci = cloudInfo[b.cloudcover] || { label: 'Clouds', color: 'rgba(0,0,0,0.08)' };
      drawBlock(topY, x0, x1, ci.label, ci.color);

        if (hasValidSeeingData) {
              if (b.seeing != null && b.seeing !== -9999) {
                  const si = seeingInfo[b.seeing] || { label: 'Seeing?', color: 'rgba(0,0,0,0.08)' };
                  drawBlock(topY + rowH + gap, x0, x1, si.label, si.color);
              } else {
                   drawBlock(topY + rowH + gap, x0, x1, '', 'rgba(0,0,0,0.08)');
              }
          }
    });
    ctx.restore();
  },

  // --- HELPER FUNCTIONS ---
  drawMessage(chart, text) {
      const { ctx, chartArea } = chart;
      if (!chartArea) return;
      const topY = chartArea.top - (18 * 2 + 2 + 6); // Same position as weather bars
      ctx.save();
      ctx.fillStyle = 'rgba(0, 0, 0, 0.5)';
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

const OBJECT_SIZE_ARCMIN = null;
let aladin = null;
let fovLayer = null;
let altitudeChart = null;

// Catalog layer for all Nova DB objects shown in Aladin
let novaObjectsCatalog = null;

/**
 * Ensures that a catalog with all Nova DB objects is created and added to Aladin.
 * Uses RA/Dec in degrees provided via NOVA_GRAPH_DATA.allObjects.
 * Each source is drawn as a small circle with the object identifier as label.
 */
function ensureNovaObjectsCatalog() {
    if (!aladin) return;            // Need Aladin initialised first
    if (novaObjectsCatalog) return; // Already built in this session

    const graphData = window.NOVA_GRAPH_DATA || {};
    const objects = Array.isArray(graphData.allObjects) ? graphData.allObjects : [];
    if (!objects.length) return;

    // Create a catalog layer that Aladin will render as markers with labels
    const cat = A.catalog({
        name: 'Nova objects',
        shape: 'circle',          // use circular markers
        sourceSize: 10,
        color: '#83b4c5',
        labelColumn: 'name',      // read label from the "name" field of each source
        displayLabel: true,       // draw the labels on the sky
        labelColor: '#83b4c5',       // dark-ish blue labels
        labelFont: '16px sans-serif', // larger font for visibility
        labelHalo: true,          // white halo to separate text from background
        labelHaloColor: '#fff',   // contrast halo for dark backgrounds
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
        const src = A.source(ra, dec, { name: labelWithPadding });
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
            chartInstance.__weather.error = 'Weather data unavailable (offline)';
            chartInstance.update('none');
        }
        return;
    }
    if (!chartInstance) {
        console.log("Weather fetch skipped (no chart).");
        return;
    }

    console.log("Fetching weather data asynchronously...");
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
            error: hasDrawableWeather ? null : 'Weather data unavailable'
        };

        // Redraw the chart to show the weather overlay
        chartInstance.update('none'); // Use 'none' to avoid animation
        console.log("Weather data fetched and chart updated.");

    } catch (err) {
        console.error('Could not fetch or apply weather data:', err);
        chartInstance.__weather.isLoading = false;
        chartInstance.__weather.error = 'Weather data unavailable';
        chartInstance.update('none');
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
        const resp = await fetch(apiUrl);
        if (!resp.ok) throw new Error(`Failed to fetch chart data: ${resp.status} ${resp.statusText}`);
        const data = await resp.json();
        function toMs(val) {
            if (typeof val === 'number') return (val < 1e12 ? val * 1000 : val);
            if (typeof val === 'string') {
                const hasOffset = /[Zz]|[+\-]\d{2}:?\d{2}$/.test(val);
                if (hasOffset) return luxon.DateTime.fromISO(val).setZone(plotTz).toMillis();
                else return luxon.DateTime.fromISO(val, {zone: plotTz}).toMillis();
            }
            return null;
        }
        const labels = data.times.map(toMs);
        const annotations = {};
        const nowMs = luxon.DateTime.now().setZone(plotTz).toMillis();
annotations.currentTimeLine = {
            type: 'line',
            borderColor: 'rgba(255, 99, 132, 0.8)', // Reddish color
            // --- ADD THESE TWO LINES ---
            borderWidth: 3,                           // Set the line thickness
            borderDash: [10, 3],                       // Define the dash pattern [dash length, gap length]
            // --- END ADD ---
            label: {
                display: true,
                content: 'Now',
                position: 'start',
                rotation: 90,
                font: { size: 10, weight: 'bold' },
                color: 'rgba(255, 99, 132, 1)',
                backgroundColor: 'rgba(255, 255, 255, 0.7)'
            },
            xMin: nowMs, // Set initial position
            xMax: nowMs, // Set initial position
            xScaleID: 'x' // Ensure it uses the correct x-axis
        };
        const baseDt = luxon.DateTime.fromISO(data.date, {zone: plotTz});
        const nextDt = baseDt.plus({days: 1});
        const cloudInfo = {
            1: {label: 'Clear', color: 'rgba(135, 206, 250, 0.15)'},
            2: {label: 'P. Clear', color: 'rgba(135, 206, 250, 0.25)'},
            3: {label: 'P. Clear', color: 'rgba(170, 170, 170, 0.2)'},
            4: {label: 'P. Clear', color: 'rgba(170, 170, 170, 0.3)'},
            5: {label: 'P. Cloudy', color: 'rgba(120, 120, 120, 0.35)'},
            6: {label: 'P. Cloudy', color: 'rgba(120, 120, 120, 0.45)'},
            7: {label: 'Cloudy', color: 'rgba(80, 80, 80, 0.5)'},
            8: {label: 'Cloudy', color: 'rgba(80, 80, 80, 0.6)'},
            9: {label: 'Overcast', color: 'rgba(50, 50, 50, 0.7)'}
        };
        const seeingInfo = {
            1: {label: 'See: Exc', color: 'rgba(0, 255, 127, 0.2)'},
            2: {label: 'See: Good', color: 'rgba(0, 255, 127, 0.3)'},
            3: {label: 'See: Good', color: 'rgba(173, 255, 47, 0.3)'},
            4: {label: 'See: Avg', color: 'rgba(255, 255, 0, 0.3)'},
            5: {label: 'See: Avg', color: 'rgba(255, 215, 0, 0.3)'},
            6: {label: 'See: Poor', color: 'rgba(255, 165, 0, 0.3)'},
            7: {label: 'See: Poor', color: 'rgba(255, 69, 0, 0.3)'},
            8: {label: 'See: Bad', color: 'rgba(255, 0, 0, 0.3)'}
        };

        // --- MODIFIED: Weather is now fetched async, so we start with an empty array ---
        const hasWeather = false;
        const forecastForOverlay = [];
        const drawableForecast = [];
        const hasDrawableWeather = false;
        // --- END MODIFICATION ---

        function wallTimeMs(baseDateTime, timeStr) {
            if (!timeStr || !timeStr.includes(':')) return null;
            const [h, m] = timeStr.split(':').map(Number);
            return baseDateTime.set({hour: h, minute: m, second: 0, millisecond: 0}).toMillis();
        }
        const sunsetTimeCurrent = wallTimeMs(baseDt, data.sun_events.current.sunset);
        let duskTime;
        const duskTimeCurrent = wallTimeMs(baseDt, data.sun_events.current.astronomical_dusk);
        if (duskTimeCurrent && sunsetTimeCurrent && duskTimeCurrent < sunsetTimeCurrent) duskTime = wallTimeMs(nextDt, data.sun_events.current.astronomical_dusk);
        else duskTime = duskTimeCurrent;
        const dawnTime = wallTimeMs(nextDt, data.sun_events.next.astronomical_dawn);
        const sunriseTime = wallTimeMs(nextDt, data.sun_events.next.sunrise);
        const sunsetTime = sunsetTimeCurrent;
        const firstMs = labels[0], lastMs = labels[labels.length - 1], originalWindowMs = lastMs - firstMs, midnightMs = luxon.DateTime.fromISO(data.date, {zone: plotTz}).plus({days: 1}).startOf('day').toMillis();
        const currentCenterMs = (firstMs + lastMs) / 2, delta = midnightMs - currentCenterMs, xMinCentered = firstMs + delta, xMaxCentered = xMinCentered + originalWindowMs;
        if (sunsetTime) annotations.sunsetLine = { type: 'line', xMin: sunsetTime, xMax: sunsetTime, borderColor: 'black', borderWidth: 1, label: { display: true, content: 'Sunset', position: 'start', rotation: 90, font: {size: 10, weight: '400'}, color: '#222', backgroundColor: 'rgba(255,255,255,0.92)', borderColor: 'rgba(0,0,0,0.15)', borderWidth: 1 } };
        if (duskTime) annotations.duskLine = { type: 'line', xMin: duskTime, xMax: duskTime, borderColor: 'black', borderWidth: 1, label: { display: true, content: 'Astronomical dusk', position: 'start', rotation: 90, font: {size: 10, weight: '400'}, color: '#222', backgroundColor: 'rgba(255,255,255,0.92)', borderColor: 'rgba(0,0,0,0.15)', borderWidth: 1 } };
        if (dawnTime) annotations.dawnLine = { type: 'line', xMin: dawnTime, xMax: dawnTime, borderColor: 'black', borderWidth: 1, label: { display: true, content: 'Astronomical dawn', position: 'start', rotation: 90, font: {size: 10, weight: '400'}, color: '#222', backgroundColor: 'rgba(255,255,255,0.92)', borderColor: 'rgba(0,0,0,0.15)', borderWidth: 1 } };
        if (sunriseTime) annotations.sunriseLine = { type: 'line', xMin: sunriseTime, xMax: sunriseTime, borderColor: 'black', borderWidth: 1, label: { display: true, content: 'Sunrise', position: 'start', rotation: 90, font: {size: 10, weight: '400'}, color: '#222', backgroundColor: 'rgba(255,255,255,0.92)', borderColor: 'rgba(0,0,0,0.15)', borderWidth: 1 } };
        if (data.transit_time && data.transit_time !== "N/A") {
            const parts = (data.transit_time || '').split(':'), th = Number(parts[0] || 0), tm = Number(parts[1] || 0);
            const t0 = baseDt.set({hour: th, minute: tm, second: 0, millisecond: 0}).toMillis(), t1 = baseDt.plus({days: 1}).set({hour: th, minute: tm, second: 0, millisecond: 0}).toMillis();
            const nightStart = duskTime, nightEnd = dawnTime, inWindow = (t) => t >= nightStart && t <= nightEnd;
            const transitMs = inWindow(t0) ? t0 : inWindow(t1) ? t1 : (Math.abs(t0 - midnightMs) < Math.abs(t1 - midnightMs) ? t0 : t1);
            annotations.transitLine = { type: 'line', xMin: transitMs, xMax: transitMs, borderColor: 'crimson', borderWidth: 2, borderDash: [6, 6], clip: false, label: { display: true, content: data.transit_time, position: 'start', rotation: 90, font: {size: 10, weight: 'bold'}, color: 'crimson', backgroundColor: 'rgba(255,255,255,0.7)' } };
        }
        const nightShade = { id: 'nightShade', beforeDraw(chart) { const {ctx, chartArea, scales} = chart; if (!chartArea) return; const left = scales.x.getPixelForValue(scales.x.min), right = scales.x.getPixelForValue(scales.x.max); const duskPx = duskTime ? scales.x.getPixelForValue(duskTime) : right, dawnPx = dawnTime ? scales.x.getPixelForValue(dawnTime) : left; ctx.save(); ctx.fillStyle = 'rgba(211, 211, 211, 1)'; if (duskPx < dawnPx) { if (duskPx > left) ctx.fillRect(left, chartArea.top, duskPx - left, chartArea.height); if (dawnPx < right) ctx.fillRect(dawnPx, chartArea.top, right - dawnPx, chartArea.height); } else ctx.fillRect(left, chartArea.top, right - left, chartArea.height); ctx.restore(); } };
        const ctx = document.getElementById('altitudeChartCanvas').getContext('2d');
        if (window.altitudeChart) window.altitudeChart.destroy();
        window.altitudeChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels,
                datasets: [
                    { label: `${NOVA_GRAPH_DATA.objectName} Altitude`, data: data.object_alt, borderColor: '#36A2EB', yAxisID: 'yAltitude', borderWidth: 4, pointRadius: 0, tension: 0.1 },
                    { label: 'Moon Altitude', data: data.moon_alt, borderColor: '#FFC107', yAxisID: 'yAltitude', borderWidth: 4, pointRadius: 0, tension: 0.1 },
                    { label: 'Horizon Mask', data: data.horizon_mask_alt, borderColor: '#636e72', backgroundColor: 'rgba(99, 110, 114, 0.3)', yAxisID: 'yAltitude', borderWidth: 2, pointRadius: 0, tension: 0.1, fill: 'start' },
                    { label: 'Horizon', data: Array(labels.length).fill(0), borderColor: 'black', yAxisID: 'yAltitude', borderWidth: 2, pointRadius: 0 },
                    { label: `${NOVA_GRAPH_DATA.objectName} Azimuth`, data: data.object_az, borderColor: '#36A2EB', yAxisID: 'yAzimuth', borderDash: [5, 5], borderWidth: 3.5, pointRadius: 0, tension: 0.1 },
                    { label: 'Moon Azimuth', data: data.moon_az, borderColor: '#FFC107', yAxisID: 'yAzimuth', borderDash: [5, 5], borderWidth: 3.5, pointRadius: 0, tension: 0.1 }
                ]
            },
            plugins: [nightShade, weatherOverlayPlugin], // Always include weather plugin
            options: {
                responsive: true,
                maintainAspectRatio: true,
                aspectRatio: 2,
                adapters: {date: {zone: plotTz}},
                layout: { padding: { top: 46 } }, // Always reserve space
                plugins: {
                    annotation: {annotations},
                    legend: {position: 'right'},
                    title: {
                        display: false,
                        text: `Altitude and Azimuth for ${NOVA_GRAPH_DATA.altName} on ${data.date}`,
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
                        title: {display: true, text: `Time (Local - ${NOVA_GRAPH_DATA.plotLocName})`}
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
            }
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
            const urls = months.map(m => `/api/get_monthly_plot_data/${encodeURIComponent(objectName)}?year=${year}&month=${m}&plot_lat=${plotLat}&plot_lon=${plotLon}&plot_tz=${encodeURIComponent(plotTz)}`);
            const responses = await Promise.all(urls.map(u => fetch(u)));
            const bad = responses.find(r => !r.ok);
            if (bad) throw new Error(`HTTP ${bad.status} for ${bad.url}`);
            const monthly = await Promise.all(responses.map(r => r.json()));
            monthly.forEach(block => { block.dates.forEach((d, i) => { const t = `${d}T00:00:00`; objAlt.push({x: t, y: block.object_alt[i]}); moonAlt.push({x: t, y: block.moon_alt[i]}); horizon.push({x: t, y: 0}); }); });
        } else {
            titleText = `Monthly Altitude at Local Midnight for ${NOVA_GRAPH_DATA.altName} - ${year}-${selMonth}`;
            const apiUrl = `/api/get_monthly_plot_data/${encodeURIComponent(objectName)}?year=${year}&month=${selMonth}&plot_lat=${plotLat}&plot_lon=${plotLon}&plot_tz=${encodeURIComponent(plotTz)}`;
            const resp = await fetch(apiUrl);
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            data.dates.forEach((d, i) => { const t = `${d}T00:00:00`; objAlt.push({x: t, y: data.object_alt[i]}); moonAlt.push({x: t, y: data.moon_alt[i]}); horizon.push({x: t, y: 0}); });
        }
        const ctx = document.getElementById('altitudeChartCanvas').getContext('2d');
        if (window.altitudeChart) window.altitudeChart.destroy();
        window.altitudeChart = new Chart(ctx, { type: 'line', data: { datasets: [ { label: `${NOVA_GRAPH_DATA.objectName} Altitude`, data: objAlt, borderColor: '#36A2EB', borderWidth: 3, pointRadius: 0, tension: 0.2 }, { label: 'Moon Altitude', data: moonAlt, borderColor: 'gold', borderWidth: 2.5, pointRadius: 0, tension: 0.0 }, {label: 'Horizon', data: horizon, borderColor: 'black', borderWidth: 2, pointRadius: 0} ] }, options: { adapters: {date: {zone: plotTz}}, responsive: true, maintainAspectRatio: true, aspectRatio: 2, scales: { x: { type: 'time', time: { unit: (view === 'year') ? 'month' : 'day', displayFormats: (view === 'year') ? {month: 'MMM'} : {day: 'dd'} }, title: { display: true, text: (view === 'year') ? `Month of ${year}` : `Day of ${year}-${selMonth}` } }, y: { min: -90, max: 90, title: {display: true, text: 'Altitude (°)'} } }, plugins: { legend: {position: 'right'}, title: {display: false, text: titleText, align: 'start', font: {size: 16}} } } });
    } catch (err) {
        console.error(`Could not render ${view} chart:`, err);
        const canvas = document.getElementById('altitudeChartCanvas');
        if (canvas) { const c = canvas.getContext('2d'); c.clearRect(0, 0, canvas.width, canvas.height); c.font = "16px Arial"; c.fillStyle = "red"; c.textAlign = "center"; c.fillText(`Error loading ${view} chart data.`, canvas.width / 2, canvas.height / 2); }
    } finally { chartLoadingDiv?.classList.add('hidden'); }
}

let baseSurvey = null;
let blendLayer = null;
let geoBeltLayer = null; // Layer for satellite belt
let fovCenter = null;
let lockToObject = false;
let objectCoords = null;
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
        const rotation = Math.max(-90, Math.min(90, Number(val) || 0));
        if (typeof lockToObject !== 'undefined' && lockToObject) {
            const sel = document.getElementById('framing-rig-select');
            if (sel && sel.selectedIndex >= 0) { const opt = sel.options[sel.selectedIndex]; updateScreenFovOverlay(opt.dataset.fovw, opt.dataset.fovh, rotation); }
        }
        if (typeof window.updateFramingChart === 'function') window.updateFramingChart(false);
    });
}
function onRotationInput(val) {
    // Allow full 0-360 range to match ASIAIR PA
    let v = Number(val) || 0;
    // Normalize to 0-360 just in case
    v = ((v % 360) + 360) % 360;

    const span = document.getElementById('rotation-value');
    if (span) span.textContent = v.toFixed(1).replace(/\.0$/, '') + '°';
    scheduleRotationUpdate(v);
}
function toggleFramingFullscreen(btn) {
    const modalContent = document.getElementById('framing-modal-content');
    if (!modalContent) return;
    modalContent.classList.toggle('fullscreen');
    const isFullscreen = modalContent.classList.contains('fullscreen');
    if (btn) { btn.innerHTML = isFullscreen ? '&#x21F2;' : '&#x2922;'; btn.title = isFullscreen ? 'Exit fullscreen' : 'Fullscreen'; }
}
function openFramingAssistant(optionalQueryString) {
    const framingModal = document.getElementById('framing-modal');
    framingModal.style.display = 'block'; // Show the modal frame immediately

    // --- MODIFIED: Check for internet connection first ---
    if (!navigator.onLine) {
        displayOfflineMessage('aladin-lite-div', 'The Framing Assistant requires an active internet connection to load sky surveys.');
        return; // Stop execution if offline
    }
    // --- END OF MODIFICATION ---

    const framingRigSelect = document.getElementById('framing-rig-select');
    if (framingRigSelect.options.length === 0 || framingRigSelect.value === "") { alert("Please configure at least one rig on the Configuration page first."); return; }

    (function ensureRotationReadout(){ const slider = document.getElementById('framing-rotation'); if (!slider) return; slider.setAttribute('min', '0'); slider.setAttribute('max', '360'); slider.setAttribute('step', '0.5'); if (!slider.hasAttribute('value')) slider.setAttribute('value', '0'); let n = slider.nextSibling; while (n && n.nodeType === Node.TEXT_NODE) { const t = n.textContent.trim(), next = n.nextSibling; if (t === '' || t === '0°') n.parentNode.removeChild(n); else break; n = next; } const existingSpans = Array.from(document.querySelectorAll('#rotation-value')); let span = existingSpans[0]; if (existingSpans.length > 1) existingSpans.slice(1).forEach(el => el.remove()); if (!span) { span = document.createElement('span'); span.id = 'rotation-value'; span.style.marginLeft = '8px'; span.style.fontWeight = 'normal'; span.style.fontSize = '15px'; slider.insertAdjacentElement('afterend', span); try { span.style.fontWeight = 'normal'; } catch(_) {} } try { span.style.cursor = 'pointer'; span.title = 'Tap to reset rotation to 0°'; span.addEventListener('click', () => { const slider = document.getElementById('framing-rotation'); if (!slider) return; slider.value = '0'; slider.dispatchEvent(new Event('input', { bubbles: true })); }, { once: false }); } catch (e) {} })();
    if (!aladin) {
        aladin = A.aladin('#aladin-lite-div', { survey: "P/DSS2/color", fov: 1.5, cooFrame: 'ICRS', showFullscreenControl: false, showGotoControl: false });
        (function installSlowWheelZoom(){ if (window.__novaSlowZoomInstalled) return; const host = document.getElementById('aladin-lite-div'); if (!host) return; try { host.style.overscrollBehavior = 'contain'; } catch(e) {} function onWheel(ev) { if (ev.ctrlKey) return; ev.preventDefault(); ev.stopPropagation(); if (!aladin) return; const unit = (ev.deltaMode === 1) ? 16 : (ev.deltaMode === 2) ? 400 : 1; let dy = (ev.deltaY || 0) * unit; dy = Math.max(-80, Math.min(80, dy)); const g = aladin.getFov(), current = Array.isArray(g) ? (g[0] ?? 1) : (g ?? 1); const scale = Math.exp(dy * 0.00075), minFov = 0.01, maxFov = 180; const next = Math.min(maxFov, Math.max(minFov, current * scale)); if (Number.isFinite(next)) aladin.setFov(next); } host.addEventListener('wheel', onWheel, { passive: false, capture: true }); const tryBindCanvas = () => { const cv = host.querySelector('canvas'); if (cv) cv.addEventListener('wheel', onWheel, { passive: false, capture: true }); }; tryBindCanvas(); setTimeout(tryBindCanvas, 50); window.__novaSlowZoomInstalled = true; })();
        baseSurvey = aladin.getBaseImageLayer();
        let __blendSurveyId = null;
        function ensureBlendLayer() {
            if (!aladin) return null;
            const sel = document.getElementById('blend-survey-select');
            if (!sel) return null;

            const surveyId = sel.value;
            const existing = aladin.getOverlayImageLayer && aladin.getOverlayImageLayer('blend');
            if (existing && __blendSurveyId === surveyId) {
                return existing;
            }

            try {
                let hpx;
                if (surveyId.startsWith('http')) {
                    const friendlyName = sel.options[sel.selectedIndex]?.textContent || surveyId;
                    hpx = aladin.createImageSurvey(surveyId, friendlyName, surveyId, "equatorial", 9);
                } else {
                    hpx = (aladin.newImageSurvey) ? aladin.newImageSurvey(surveyId) : aladin.createImageSurvey(surveyId, surveyId, surveyId, 'equatorial', 9, { imgFormat: 'jpeg' });
                }
                if (hpx) {
                    aladin.setOverlayImageLayer(hpx, 'blend');
                    __blendSurveyId = surveyId;
                    return aladin.getOverlayImageLayer('blend');
                }
            } catch (e) {
                console.warn('[nova] Could not create/set overlay image survey:', e);
            }
            return null;
        }
        function setBlendOpacity(a) { const v = Math.max(0, Math.min(1, Number(a) || 0)); if (!aladin) return; const layer = ensureBlendLayer(); if (layer && (typeof layer.setOpacity === 'function' || typeof layer.setAlpha === 'function')) { (layer.setOpacity || layer.setAlpha).call(layer, v); return; } try { const survey = aladin.getOverlayImageLayer && aladin.getOverlayImageLayer('blend'); survey?.setOpacity?.(v); } catch(e) {} }
        (function wireBlendAndConstellationUI(){ const blendSel = document.getElementById('blend-survey-select'), blendOp = document.getElementById('blend-opacity'); if (blendSel) blendSel.addEventListener('change', () => { ensureBlendLayer(); const v = Number(blendOp?.value || 0); setBlendOpacity(v); updateFramingChart(false); }); if (blendOp) { const sync = (e) => { setBlendOpacity(e.target.value); updateFramingChart(false); }; blendOp.addEventListener('input', sync); blendOp.addEventListener('change', sync); } try { if (blendOp) setBlendOpacity(blendOp.value); } catch(e) {} })();
        if (aladin.on) aladin.on('zoomChanged', () => { if (!lockToObject) return; const sel = document.getElementById('framing-rig-select'), rot = parseFloat(document.getElementById('framing-rotation')?.value || '0') || 0; if (sel && sel.selectedIndex >= 0) { const opt = sel.options[sel.selectedIndex]; updateScreenFovOverlay(opt.dataset.fovw, opt.dataset.fovh, rot); } });
        if (aladin.on) aladin.on('baseLayerChanged', () => { try { const bop = document.getElementById('blend-opacity'); ensureBlendLayer(); if (bop) setBlendOpacity(bop.value); } catch (e) { console.warn('[nova] Could not reapply blend after base change:', e); } });
        fovLayer = A.graphicOverlay({color: '#83b4c5', lineWidth: 3});
        aladin.addOverlay(fovLayer);
        const canvas = document.getElementById('aladin-lite-div');
        if (window.ResizeObserver && canvas) { let roTimer = null; const ro = new ResizeObserver(() => { clearTimeout(roTimer); roTimer = setTimeout(() => { const sel = document.getElementById('framing-rig-select'); if (sel && sel.selectedIndex >= 0) { const opt = sel.options[sel.selectedIndex]; applyRigFovZoom(opt.dataset.fovw, opt.dataset.fovh); } updateFramingChart(false); if (lockToObject) { const sel = document.getElementById('framing-rig-select'), rot = parseFloat(document.getElementById('framing-rotation')?.value || '0') || 0; if (sel && sel.selectedIndex >= 0) { const opt = sel.options[sel.selectedIndex]; updateScreenFovOverlay(opt.dataset.fovw, opt.dataset.fovh, rot); } } }, 80); }); ro.observe(canvas); }
        (function ensureScreenOverlay(){ const host = document.getElementById('aladin-lite-div'); if (!host) return; if (!host.style.position) host.style.position = 'relative'; if (!document.getElementById('screen-fov-overlay')) { const ov = document.createElement('div'); ov.id = 'screen-fov-overlay'; ov.style.position = 'absolute'; ov.style.inset = '0'; ov.style.pointerEvents = 'none'; ov.style.zIndex = '5'; const rect = document.createElement('div'); rect.id = 'screen-fov-rect'; rect.style.position = 'absolute'; rect.style.border = '3px solid #83b4c5'; rect.style.boxSizing = 'border-box'; rect.style.left = '50%'; rect.style.top = '50%'; rect.style.transformOrigin = 'center center'; rect.style.display = 'none'; ov.appendChild(rect); host.appendChild(ov); } })();
        canvas.addEventListener('click', (ev) => { if (!ev.shiftKey) return; if (lockToObject) return; const rect = canvas.getBoundingClientRect(), x = ev.clientX - rect.left, y = ev.clientY - rect.top; const sky = aladin.pix2world(x, y); if (!sky) return; fovCenter = {ra: sky[0], dec: sky[1]}; updateFramingChart(false); if (lockToObject) { const sel = document.getElementById('framing-rig-select'), rot = parseFloat(document.getElementById('framing-rotation')?.value || '0') || 0; if (sel && sel.selectedIndex >= 0) { const opt = sel.options[sel.selectedIndex]; updateScreenFovOverlay(opt.dataset.fovw, opt.dataset.fovh, rot); } } updateReadoutFromCenter(); });
        window.addEventListener('keydown', (e) => {
        // Check if the user is currently typing in a form element or Trix editor
        const activeTag = document.activeElement.tagName.toLowerCase();
        const isTyping = activeTag === 'input' ||
                         activeTag === 'textarea' ||
                         activeTag === 'trix-editor' ||
                         document.activeElement.isContentEditable;

        if (isTyping) return; // Exit immediately if the user is typing

        const k = e.key.toLowerCase();
        if (['arrowup', 'arrowdown', 'arrowleft', 'arrowright', 'w', 'a', 's', 'd'].includes(k)) {
            e.preventDefault();
            if (k === 'arrowup' || k === 'w') nudgeFov(0, +1);
            if (k === 'arrowdown' || k === 's') nudgeFov(0, -1);
            if (k === 'arrowleft' || k === 'a') nudgeFov(-1, 0);
            if (k === 'arrowright' || k === 'd') nudgeFov(+1, 0);
        }
    });
        (function wireRotationLiveUpdate(){ const rotInput = document.getElementById('framing-rotation'); if (!rotInput) return; const handler = () => { const raw = (typeof rotInput.valueAsNumber === 'number') ? rotInput.valueAsNumber : parseFloat(rotInput.value) || 0; const snapped = (Math.abs(raw) <= 1) ? 0 : raw; if (snapped !== raw) rotInput.value = String(snapped); updateFramingChart(false); }; try { rotInput.removeEventListener('input', rotInput.__novaRotHandler); } catch(e) {} try { rotInput.removeEventListener('change', rotInput.__novaRotHandler); } catch(e) {} rotInput.__novaRotHandler = handler; rotInput.addEventListener('input', handler); rotInput.addEventListener('change', handler); try { rotInput.setAttribute('value', String(rotInput.valueAsNumber ?? rotInput.value ?? 0)); } catch(e) {} handler(); })();
        (function wireInsertIntoProject(){ const btn = document.getElementById('insert-into-project'); if (!btn) return; try { btn.removeEventListener('click', btn.__novaInsertHandler); } catch(e) {} btn.__novaInsertHandler = (ev) => { try { const q = buildFramingQuery(), href = location.pathname + q; history.replaceState(null, '', href); } catch(e) { console.warn('[nova] Insert-to-project wiring error:', e); } }; btn.addEventListener('click', btn.__novaInsertHandler); })();
    }
    function buildFramingQuery() { const sel = document.getElementById('framing-rig-select'), rig = sel && sel.selectedIndex >= 0 ? sel.options[sel.selectedIndex].value : '', rotInput = document.getElementById('framing-rotation'), rot = rotInput ? (parseFloat(rotInput.value) || 0) : 0, sSel = document.getElementById('survey-select'), survey = sSel ? sSel.value : '', bSel = document.getElementById('blend-survey-select'), bOp = document.getElementById('blend-opacity'), blend = bSel ? bSel.value : '', blend_op = bOp ? (parseFloat(bOp.value) || 0) : 0; const { ra, dec } = (fovCenter || (aladin && (() => { const rc = aladin.getRaDec(); return { ra: rc[0], dec: rc[1] }; })()) || { ra: NaN, dec: NaN });
        const cols = document.getElementById('mosaic-cols')?.value || 1;
    const rows = document.getElementById('mosaic-rows')?.value || 1;
    const overlap = document.getElementById('mosaic-overlap')?.value || 10;

    const qp = new URLSearchParams();
    if (rig) qp.set('rig', rig);
    if (Number.isFinite(ra)) qp.set('ra', ra.toFixed(6));
    if (Number.isFinite(dec)) qp.set('dec', dec.toFixed(6));
    qp.set('rot', String(Math.round(to360(rot))));
    if (survey) qp.set('survey', survey);
    if (blend) qp.set('blend', blend);
    qp.set('blend_op', String(Math.max(0, Math.min(1, blend_op))));

    // Mosaic Params
    if (cols > 1 || rows > 1) {
        qp.set('m_cols', cols);
        qp.set('m_rows', rows);
        qp.set('m_ov', overlap);
    }

    return '?' + qp.toString();
}

    let haveCenter = false, haveRot = false, haveRigRestored = false;
    try {
        // --- FIX: Use optionalQueryString if provided, otherwise fallback to location.search ---
        const q = new URLSearchParams(optionalQueryString || location.search);
        // -------------------------------------------------------------------------------------

        const rig = q.get('rig'), ra = parseFloat(q.get('ra')), dec = parseFloat(q.get('dec')), rot = parseFloat(q.get('rot')), surv = q.get('survey'), blend = q.get('blend'), blendOp = parseFloat(q.get('blend_op'));

        // Restore Mosaic
        if (q.has('m_cols')) document.getElementById('mosaic-cols').value = q.get('m_cols');
        if (q.has('m_rows')) document.getElementById('mosaic-rows').value = q.get('m_rows');
        if (q.has('m_ov')) document.getElementById('mosaic-overlap').value = q.get('m_ov');
        if (rig) { const sel = document.getElementById('framing-rig-select'); if (sel) { const idx = Array.from(sel.options).findIndex(o => o.value === rig); if (idx >= 0) { sel.selectedIndex = idx; haveRigRestored = true; } } }
        if (!Number.isNaN(rot)) {
            const rotInput = document.getElementById('framing-rotation');
            // Normalize to 0-360 positive
            const normRot = (rot % 360 + 360) % 360;
            if (rotInput) rotInput.value = normRot;
            const rotSpan = document.getElementById('rotation-value');
            if (rotSpan) rotSpan.textContent = `${Math.round(normRot)}°`;
            haveRot = true;
        }
        if (surv) {
            // Explicitly sync the dropdown UI first
            const sSel = document.getElementById('survey-select');
            if (sSel) sSel.value = surv;
            // Then update the Aladin layer
            if (typeof setSurvey === 'function') setSurvey(surv);
        }
        try { const bsel = document.getElementById('blend-survey-select'), bop = document.getElementById('blend-opacity'); if (blend && bsel) { bsel.value = blend; if (typeof ensureBlendLayer === 'function') ensureBlendLayer(); } if (!Number.isNaN(blendOp) && bop) { bop.value = String(Math.max(0, Math.min(1, blendOp))); if (typeof setBlendOpacity === 'function') setBlendOpacity(bop.value); } } catch (e) {}
        try { const bop2 = document.getElementById('blend-opacity'); ensureBlendLayer(); if (bop2) setBlendOpacity(bop2.value); } catch (e) {}
        if (!Number.isNaN(ra) && !Number.isNaN(dec)) { fovCenter = {ra, dec}; haveCenter = true; } else fovCenter = null;
    } catch (e) {}
    try { const bop = document.getElementById('blend-opacity'); ensureBlendLayer(); if (bop) setBlendOpacity(bop.value); } catch (e) {}
    if (!haveRot) { const rotInput = document.getElementById('framing-rotation'); if (rotInput) rotInput.value = 0; const rotSpan = document.getElementById('rotation-value'); if (rotSpan) rotSpan.textContent = `0°`; }
    if (haveCenter) { const lockBox = document.getElementById('lock-to-object'); if (lockBox) lockBox.checked = false; lockToObject = false; if (aladin && typeof aladin.gotoRaDec === 'function') aladin.gotoRaDec(fovCenter.ra, fovCenter.dec); if (haveCenter) { const sel = document.getElementById('framing-rig-select'); if (sel && sel.selectedIndex >= 0) { const opt = sel.options[sel.selectedIndex]; applyRigFovZoom(opt.dataset.fovw, opt.dataset.fovh); } } }
    updateFramingChart(haveCenter ? false : true);
    updateFovVsObjectLabel?.();
    updateReadoutFromCenter?.();

    try {
        ensureNovaObjectsCatalog();
    } catch (e) {
        console.error('Failed to build Nova objects catalog for Aladin:', e);
    }

    if (!haveCenter) applyLockToObject(true);

    // Restore Geo Belt State
    const geoCheck = document.getElementById('show-geo-belt');
    if (geoCheck && geoCheck.checked) {
        toggleGeoBelt(true);
    }
}
function closeFramingAssistant() { document.getElementById('framing-modal').style.display = 'none'; }
function flipFraming90() { const slider = document.getElementById('framing-rotation'); let v = parseFloat(slider.value) || 0; v += 90; v = v % 360; slider.value = v; slider.dispatchEvent(new Event('input', { bubbles: true })); updateFramingChart(false); if (typeof updateReadoutFromCenter === 'function') updateReadoutFromCenter(); }

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
        // 2. Calculate Declination
        const rawLat = window.NOVA_GRAPH_DATA.plotLat || 0;
        const latDeg = parseFloat(rawLat) || 0;

        const latRad = latDeg * Math.PI / 180;
        const Re = 6378;
        const Rgeo = 42164;
        const num = Re * Math.sin(latRad);
        const den = Rgeo - (Re * Math.cos(latRad));
        const parallaxRad = Math.atan2(num, den);

        let apparentDec = -(parallaxRad * 180 / Math.PI);
        if (isNaN(apparentDec)) apparentDec = 0;

        console.log(`[Nova] Drawing Geo Belt (Catalog) at Dec: ${apparentDec.toFixed(2)}°`);

        // 3. Create CATALOG Layer
        geoBeltLayer = A.catalog({
            name: 'Geostationary Belt',
            color: '#e056fd',
            sourceSize: 6,   // Safe minimum size to prevent canvas crash
            shape: 'circle'
        });
        aladin.addCatalog(geoBeltLayer);

        // 4. Generate High-Density Points (Simulates a line)
        let sources = [];
        // Create a point every 0.2 degrees (High density)
        for (let ra = 0; ra < 360; ra += 0.2) {
            sources.push(A.source(ra, apparentDec, {name: ''}));
        }
        geoBeltLayer.addSources(sources);

    } catch (e) {
        console.error("[Nova] Critical Error in toggleGeoBelt:", e);
    }
}

function applyRigFovZoom(fovW_arcmin, fovH_arcmin, rotationDeg = 0, margin = 1.06) {
    if (!aladin) return; const host = document.getElementById('aladin-lite-div'); if (!host) return;
    const wpx = host.clientWidth, hpx = host.clientHeight; if (!(wpx > 0 && hpx > 0)) return; const aspect = wpx / hpx;
    const wDeg = parseFloat(fovW_arcmin) / 60, hDeg = parseFloat(fovH_arcmin) / 60; if (!(isFinite(wDeg) && isFinite(hDeg) && wDeg > 0 && hDeg > 0)) return;
    const th = (parseFloat(rotationDeg) || 0) * Math.PI / 180;
    const needWidthDeg = Math.abs(wDeg * Math.cos(th)) + Math.abs(hDeg * Math.sin(th)), needHeightDeg = Math.abs(wDeg * Math.sin(th)) + Math.abs(hDeg * Math.cos(th));
    const requiredWidthDeg = Math.max(needWidthDeg * margin, needHeightDeg * margin * aspect);
    aladin.setFov(requiredWidthDeg);
    if (lockToObject) updateScreenFovOverlay(fovW_arcmin, fovH_arcmin, rotationDeg);
}
window.updateFramingChart = function (recenter = true) {
    if (!aladin) return;
    const objectName = NOVA_GRAPH_DATA.objectName, framingRigSelect = document.getElementById('framing-rig-select'), rotationSlider = document.getElementById('framing-rotation'), selectedOption = framingRigSelect.options[framingRigSelect.selectedIndex];
    if (!selectedOption) return;
    const fovWidthArcmin = parseFloat(selectedOption.dataset.fovw), fovHeightArcmin = parseFloat(selectedOption.dataset.fovh);
    const vNum = (rotationSlider && typeof rotationSlider.valueAsNumber === 'number') ? rotationSlider.valueAsNumber : NaN;
    const rotation = Number.isFinite(vNum) ? vNum : (Number.isFinite(parseFloat(rotationSlider.value)) ? parseFloat(rotationSlider.value) : 0);

    // Update badge with simple 0-360 value
    (function updateRotationBadge(){ const el = document.getElementById('rotation-value'), sliderEl = document.getElementById('framing-rotation'), txt = `${Math.round(rotation)}°`; if (el) el.textContent = txt; if (sliderEl) sliderEl.title = `Rotation: ${txt}`; })();

    if (recenter) applyRigFovZoom(fovWidthArcmin, fovHeightArcmin, rotation);
    if (recenter) {
        // --- START OF FIX ---
        // Get the coordinates from the global object (set in graph_view.html)
        const raDeg = window.NOVA_GRAPH_DATA.objectRADeg;
        const decDeg = window.NOVA_GRAPH_DATA.objectDECDeg;
        const objectName = window.NOVA_GRAPH_DATA.objectName; // Also get objectName from here

        // Check if we have valid, manually-provided coordinates from our database
        if (raDeg != null && decDeg != null && isFinite(raDeg) && isFinite(decDeg)) {

            // We have coords! Use gotoRaDec (which uses degrees) instead of gotoObject
            aladin.gotoRaDec(raDeg, decDeg);

            // Manually trigger the "success" logic
            applyRigFovZoom(fovWidthArcmin, fovHeightArcmin, rotation);
            const rc = aladin.getRaDec(); // Get the coords we just set
            objectCoords = {ra: rc[0], dec: rc[1]};
            fovCenter = lockToObject ? {...objectCoords} : {ra: rc[0], dec: rc[1]};
            if (lockToObject) {
                if (fovLayer) fovLayer.removeAll();
                updateScreenFovOverlay(fovWidthArcmin, fovHeightArcmin, rotation);
            } else {
                drawFovFootprint(fovWidthArcmin, fovHeightArcmin, rotation, fovCenter);
            }
            updateReadoutFromCenter?.();

        } else {
            // No manual coords, fall back to default Aladin/SIMBAD lookup
            aladin.gotoObject(objectName, {
                success: () => {
                    // (Original success logic)
                    applyRigFovZoom(fovWidthArcmin, fovHeightArcmin, rotation);
                    const rc = aladin.getRaDec();
                    objectCoords = {ra: rc[0], dec: rc[1]};
                    fovCenter = lockToObject ? {...objectCoords} : {ra: rc[0], dec: rc[1]};
                    if (lockToObject) { if (fovLayer) fovLayer.removeAll(); updateScreenFovOverlay(fovWidthArcmin, fovHeightArcmin, rotation); }
                    else drawFovFootprint(fovWidthArcmin, fovHeightArcmin, rotation, fovCenter);
                    updateReadoutFromCenter?.();
                },
                error: () => {
                    // (Original error logic)
                    const rc = aladin.getRaDec();
                    fovCenter = {ra: rc[0], dec: rc[1]};
                    if (lockToObject) { if (fovLayer) fovLayer.removeAll(); updateScreenFovOverlay(fovWidthArcmin, fovHeightArcmin, rotation); }
                    else drawFovFootprint(fovWidthArcmin, fovHeightArcmin, rotation, fovCenter);
                    updateReadoutFromCenter?.();
                }
            });
        }
        return; // End of 'recenter' block
        // --- END OF FIX ---
    }
    if (!fovCenter) { const rc = aladin.getRaDec(); fovCenter = {ra: rc[0], dec: rc[1]}; }
    if (lockToObject) { if (fovLayer) fovLayer.removeAll(); updateScreenFovOverlay(fovWidthArcmin, fovHeightArcmin, rotation); }
    else drawFovFootprint(fovWidthArcmin, fovHeightArcmin, rotation, fovCenter);
};
function saveFramingToDB() {
    const objectName = NOVA_GRAPH_DATA.objectName;
    const sel = document.getElementById('framing-rig-select');

    // 1. Gather Data
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
        mosaic_overlap: parseFloat(document.getElementById('mosaic-overlap')?.value || 10)
    };

    // 2. Send to DB
    fetch('/api/save_framing', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
    .then(r => r.json())
    .then(data => {
        if(data.status === 'success') {
            alert("Framing settings saved to database.");
            checkAndShowFramingButton(); // Refresh the button immediately
        } else {
            alert("Error saving: " + data.message);
        }
    });
}

function checkAndShowFramingButton() {
    const objectName = NOVA_GRAPH_DATA.objectName;
    const container = document.getElementById('project-quick-link');
    if (!container) return;

    fetch(`/api/get_framing/${encodeURIComponent(objectName)}`)
        .then(r => r.json())
        .then(data => {
            container.innerHTML = ''; // Clear any existing button

            if (data.status === 'found') {
                const btn = document.createElement('button');
                btn.className = 'inline-button';
                btn.textContent = 'Open Saved Framing';

                // --- COLOR CHANGE: Removed explicit green color ---
                // It now inherits the standard #83b4c5 from the 'inline-button' class

                // Keep size overrides if you want it slightly smaller than the main buttons
                btn.style.fontSize = '13px';
                btn.style.padding = '6px 12px';

                btn.onclick = () => {
                    // Reconstruct query string from DB data
                    const params = new URLSearchParams();
                    if(data.rig) params.set('rig', data.rig);
                    if(data.ra) params.set('ra', data.ra);
                    if(data.dec) params.set('dec', data.dec);
                    params.set('rot', data.rotation);
                    if(data.survey) params.set('survey', data.survey);
                    if(data.blend) params.set('blend', data.blend);
                    params.set('blend_op', data.blend_op);

                    // Restore Mosaic
                    if(data.mosaic_cols) params.set('m_cols', data.mosaic_cols);
                    if(data.mosaic_rows) params.set('m_rows', data.mosaic_rows);
                    if(data.mosaic_overlap) params.set('m_ov', data.mosaic_overlap);

                    openFramingAssistant(params.toString());
                };
                container.appendChild(btn);
            }
        });
}
function drawFovFootprint(fovWidthArcmin, fovHeightArcmin, rotationDeg, center) {
    if (!aladin || !fovLayer || !center) return;
    fovLayer.removeAll();

    const cols = parseInt(document.getElementById('mosaic-cols')?.value || 1);
    const rows = parseInt(document.getElementById('mosaic-rows')?.value || 1);
    const overlap = parseFloat(document.getElementById('mosaic-overlap')?.value || 0) / 100;

    // Pane dimensions in degrees
    const wDeg = (fovWidthArcmin / 60);
    const hDeg = (fovHeightArcmin / 60);
    const halfW = wDeg / 2;
    const halfH = hDeg / 2;

    // Step sizes (effective width/height after overlap)
    const wStep = wDeg * (1 - overlap);
    const hStep = hDeg * (1 - overlap);

    // Invert angle to match CSS rotation (CSS is Clockwise, Standard Math is Counter-Clockwise)
    const ang = -rotationDeg * Math.PI / 180;
    const ra0 = center.ra * Math.PI / 180, dec0 = center.dec * Math.PI / 180;

    // Tangent plane vectors at center
    const cX = Math.cos(dec0) * Math.cos(ra0), cY = Math.cos(dec0) * Math.sin(ra0), cZ = Math.sin(dec0);
    const eX = -Math.sin(ra0), eY = Math.cos(ra0), eZ = 0;
    const nX = -Math.sin(dec0) * Math.cos(ra0), nY = -Math.sin(dec0) * Math.sin(ra0), nZ = Math.cos(dec0);

    function rot2d(x, y) {
        return [x * Math.cos(ang) - y * Math.sin(ang), x * Math.sin(ang) + y * Math.cos(ang)];
    }

    function planeToSky(x_deg, y_deg) {
        const dx = x_deg * Math.PI / 180, dy = y_deg * Math.PI / 180, r = Math.hypot(dx, dy);
        if (r < 1e-12) return [center.ra, center.dec];
        const dirX = (dx * eX + dy * nX) / r, dirY = (dx * eY + dy * nY) / r, dirZ = (dx * eZ + dy * nZ) / r;
        const s = Math.sin(r), c = Math.cos(r);
        const pX = c * cX + s * dirX, pY = c * cY + s * dirY, pZ = c * cZ + s * dirZ;
        let ra = Math.atan2(pY, pX); if (ra < 0) ra += 2 * Math.PI; const dec = Math.asin(pZ);
        return [ra * 180 / Math.PI, dec * 180 / Math.PI];
    }

    // Generate Mosaic Grid
    for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
            // Calculate offset of this pane's center from mosaic center (unrotated)
            // We index 0,0 at bottom-left (standard sky coords, RA increases left, Dec up)
            // Visual logic: Row 0 is bottom, Col 0 is left (East if looking South/North up)
            const cx_off = (c - (cols - 1) / 2) * wStep;
            // Standard Cartesian: r=0 is bottom.
            const cy_off = (r - (rows - 1) / 2) * hStep;

            // 4 Corners relative to this pane's center
            // Note: in Aladin/Sky, X is RA. RA increases to the East (Left on chart).
            // Standard plot X increases Right.
            // We use standard XY for rotation, then map to RA/Dec.
            // Corner offsets:
            const corners = [
                [-halfW, -halfH], [halfW, -halfH], [halfW, halfH], [-halfW, halfH]
            ];

            const polyCoords = corners.map(([kx, ky]) => {
                // Offset from pane center + Pane center offset from mosaic center
                const totalX = kx + cx_off;
                const totalY = ky + cy_off;
                // Rotate around mosaic center
                const [rx, ry] = rot2d(totalX, totalY);
                // Project to sky
                return planeToSky(-rx, ry); // Negate X for RA direction if needed, usually Aladin handles standard projection
            });

            polyCoords.push(polyCoords[0]);
            const fovPolygon = A.polygon(polyCoords, {color: '#83b4c5', lineWidth: 2});
            fovLayer.add(fovPolygon);
        }
    }
    updateReadoutFromCenter?.();
}

let lockFovEnabled = false, lockRafId = null;
function updateScreenFovOverlay(fovWidthArcmin, fovHeightArcmin, rotationDeg) {
    const host = document.getElementById('aladin-lite-div');
    const rectEl = document.getElementById('screen-fov-rect');
    if (!host || !rectEl) return;

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

    // Base Pane Size in Pixels
    const panePxW = (fovWdeg / viewWdeg) * wpx;
    const panePxH = (fovHdeg / viewHdeg) * hpx;

    // Calculate Grid Dimensions
    const stepW = panePxW * (1 - overlap);
    const stepH = panePxH * (1 - overlap);

    const totalW = panePxW + (cols - 1) * stepW;
    const totalH = panePxH + (rows - 1) * stepH;

    // Configure the container (rectEl) to be the center of rotation
    // We make it 0x0 size so rotation logic is simpler around the center
    rectEl.style.display = 'block';
    rectEl.style.width = '0px';
    rectEl.style.height = '0px';
    rectEl.style.border = 'none'; // Container has no border
    rectEl.style.left = '50%';
    rectEl.style.top = '50%';
    rectEl.style.marginLeft = '0px';
    rectEl.style.marginTop = '0px';
    rectEl.style.transform = `rotate(${rotationDeg || 0}deg)`;

    // Clear previous children
    rectEl.innerHTML = '';

    // Generate Panes
    for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
            const div = document.createElement('div');

            // Calculate center offset in pixels relative to mosaic center
            // In CSS, Y increases downwards. To match the sky logic where r=0 is bottom,
            // we must map r=0 to the highest Y value (bottom of container).
            // Visual grid: (0,0) is Bottom-Left.

            const cx_off = (c - (cols - 1) / 2) * stepW;
            // Invert Y for screen coords (0 at center, +Y is down, -Y is up)
            // Sky: r=0 is bottom (negative offset). Screen: r=0 is bottom (positive Y offset).
            const cy_off = -1 * (r - (rows - 1) / 2) * stepH;

            div.style.position = 'absolute';
            div.style.boxSizing = 'border-box';
            div.style.border = '2px solid #83b4c5'; // Pane border
            if (cols > 1 || rows > 1) {
                div.style.background = 'rgba(131, 180, 197, 0.1)'; // Slight fill for mosaic
                div.innerText = `${c+1},${r+1}`;
                div.style.color = 'rgba(131, 180, 197, 0.8)';
                div.style.fontSize = '10px';
                div.style.display = 'flex';
                div.style.alignItems = 'center';
                div.style.justifyContent = 'center';
            }

            div.style.width = panePxW + 'px';
            div.style.height = panePxH + 'px';

            // Center the div at the calculated offset
            div.style.left = (cx_off - panePxW/2) + 'px';
            div.style.top = (cy_off - panePxH/2) + 'px';

            rectEl.appendChild(div);
        }
    }
}
function startLockOverlayLoop() { if (lockRafId) return; const tick = () => { if (!lockToObject) { lockRafId = null; return; } const sel = document.getElementById('framing-rig-select'), rot = parseFloat(document.getElementById('framing-rotation')?.value || '0') || 0; if (sel && sel.selectedIndex >= 0) { const opt = sel.options[sel.selectedIndex]; updateScreenFovOverlay(opt.dataset.fovw, opt.dataset.fovh, rot); } updateReadoutFromCenter(); lockRafId = requestAnimationFrame(tick); }; lockRafId = requestAnimationFrame(tick); }
function stopLockOverlayLoop() { if (lockRafId) { cancelAnimationFrame(lockRafId); lockRafId = null; } }
function setSurvey(hipsId) {
    if (!aladin) return;

    let newLayer;

    // --- THIS IS THE NEW LOGIC ---
    // Check if the ID is a full URL (external) or a simple ID (internal)
    if (hipsId.startsWith('http')) {
        // --- This is an EXTERNAL survey ---
        // We must use aladin.createImageSurvey(id, name, url, frame, order, options)
        // We derive a friendly name from the <option> text
        const friendlyName = document.querySelector(`#survey-select option[value='${hipsId}']`)?.textContent || hipsId;

        try {
            // This is the documented way to add an external HiPS survey
            newLayer = aladin.createImageSurvey(
                hipsId,         // A unique ID for this layer (the URL is fine)
                friendlyName,   // A human-readable name
                hipsId,         // The base URL for the HiPS tiles
                "equatorial",   // The coordinate frame
                9,              // A standard max order (depth)
            );
        } catch (e) {
            console.error("Error creating external survey layer:", e);
            alert("Could not load external survey. See console for details.");
            return;
        }
    } else {
        // --- This is a STANDARD, built-in survey ---
        newLayer = aladin.newImageSurvey(hipsId);
    }
    // --- END OF NEW LOGIC ---

    aladin.setBaseImageLayer(newLayer);
    baseSurvey = aladin.getBaseImageLayer();
    updateImageAdjustments();
}
function updateImageAdjustments() {
    // Read all slider values first
    const b = parseFloat(document.getElementById('img-bright').value);
    const c = parseFloat(document.getElementById('img-contrast').value);
    const g = parseFloat(document.getElementById('img-gamma').value);
    const s = parseFloat(document.getElementById('img-sat').value);

    /**
     * Helper function to apply adjustments to any Aladin layer
     * that supports these methods.
     */
    const applySettings = (layer) => {
        if (!layer) return;

        // Check for the existence of each function before calling it
        if (typeof layer.setBrightness === 'function') {
            layer.setBrightness(b);
        }
        if (typeof layer.setContrast === 'function') {
            layer.setContrast(c);
        }
        if (typeof layer.setGamma === 'function') {
            layer.setGamma(g);
        }
        if (typeof layer.setSaturation === 'function') {
            layer.setSaturation(s);
        }
    };

    // 1. Apply settings to the main base survey layer
    applySettings(baseSurvey);

    // 2. Apply the *same* settings to the blend overlay layer, if it exists
    if (aladin && typeof aladin.getOverlayImageLayer === 'function') {
        try {
            const blendLayer = aladin.getOverlayImageLayer('blend');
            applySettings(blendLayer);
        } catch (e) {
            // This might fail if the layer hasn't been created, which is fine
            // console.warn("Could not apply settings to blend layer (yet).", e);
        }
    }
}
function updateReadout(raDeg, decDeg) {
    // Convert the J2000 coordinates from Aladin to JNow for the UI display
    const jNow = convertJ2000ToJNow(raDeg, decDeg);
    document.getElementById('ra-readout').value = formatRA(jNow.ra);
    document.getElementById('dec-readout').value = formatDec(jNow.dec);
}
function updateReadoutFromCenter() { let center; if (lockToObject) { const rc = aladin.getRaDec(); center = { ra: rc[0], dec: rc[1] }; } else if (fovCenter && isFinite(fovCenter.ra) && isFinite(fovCenter.dec)) center = fovCenter; else { const rc = aladin.getRaDec(); center = { ra: rc[0], dec: rc[1] }; } updateReadout(center.ra, center.dec); }
function copyRaDec() { const text = `${document.getElementById('ra-readout').value} ${document.getElementById('dec-readout').value}`; navigator.clipboard.writeText(text); }
function changeView(view) {
    const day = document.getElementById('day-select').value, month = document.getElementById('month-select').value, year = document.getElementById('year-select').value, objectName = NOVA_GRAPH_DATA.objectName;
    fetch(`/get_date_info/${encodeURIComponent(objectName)}?day=${day}&month=${month}&year=${year}`)
        .then(response => response.json())
        .then(data => {
            document.getElementById("phase-display").innerText = data.phase + "%";
            document.getElementById("dusk-display").innerText = data.astronomical_dusk;
            document.getElementById("dawn-display").innerText = data.astronomical_dawn;
            if (data.date_display) document.getElementById("date-display").innerText = data.date_display;
        });
    if (view === 'day') renderClientSideChart();
    else renderMonthlyYearlyChart(view);
}
function useReadoutAsFovCenter() {
    const raStr = document.getElementById('ra-readout').value, decStr = document.getElementById('dec-readout').value;
    const skyJNow = parseRaDec(raStr, decStr); // Parsed as JNow
    if (!skyJNow) return;
    // Convert back to J2000 for Aladin positioning
    const skyJ2000 = convertJNowToJ2000(skyJNow.ra, skyJNow.dec);
    fovCenter = {ra: skyJ2000.ra, dec: skyJ2000.dec};
    updateFramingChart(false);
    updateReadoutFromCenter();
}
function resetFovCenterToObject() { fovCenter = null; updateFramingChart(true); updateFovVsObjectLabel(); }
function nudgeFov(dxArcmin, dyArcmin) {
    if (lockToObject && objectCoords) { fovCenter = {ra: objectCoords.ra, dec: objectCoords.dec}; updateFramingChart(false); updateReadoutFromCenter(); return; }
    if (!fovCenter) { const rc = aladin.getRaDec(); fovCenter = {ra: rc[0], dec: rc[1]}; }
    const decRad = fovCenter.dec * (Math.PI / 180);
    if (Math.abs(decRad) < (Math.PI / 2.0 - 0.001)) fovCenter.ra -= (dxArcmin / 60.0) / Math.cos(decRad);
    fovCenter.dec += dyArcmin / 60.0;
    updateFramingChart(false); updateReadoutFromCenter();
}
function applyLockToObject(locked) {
    lockToObject = !!locked;
    const rectEl = document.getElementById('screen-fov-rect');
    if (lockToObject) {
        const c = objectCoords || (() => { const rc = aladin.getRaDec(); return {ra: rc[0], dec: rc[1]}; })();
        fovCenter = {ra: c.ra, dec: c.dec};
        if (fovLayer) fovLayer.removeAll();
        if (rectEl) rectEl.style.display = 'block';
        const sel = document.getElementById('framing-rig-select'), rot = parseFloat(document.getElementById('framing-rotation')?.value || '0') || 0;
        if (sel && sel.selectedIndex >= 0) { const opt = sel.options[sel.selectedIndex]; updateScreenFovOverlay(opt.dataset.fovw, opt.dataset.fovh, rot); }
        startLockOverlayLoop(); updateReadoutFromCenter();
    } else {
        if (rectEl) rectEl.style.display = 'none';
        stopLockOverlayLoop();
        const sel = document.getElementById('framing-rig-select'), rot = parseFloat(document.getElementById('framing-rotation')?.value || '0') || 0;
        const center = fovCenter || (() => { const rc = aladin.getRaDec(); return {ra: rc[0], dec: rc[1]}; })();
        if (sel && sel.selectedIndex >= 0) { const opt = sel.options[sel.selectedIndex]; drawFovFootprint(parseFloat(opt.dataset.fovw), parseFloat(opt.dataset.fovh), rot, center); }
        updateReadoutFromCenter();
    }
}
function updateFovVsObjectLabel() { const el = document.getElementById('fov-vs-object'); if (!el) return; const sel = document.getElementById('framing-rig-select'); if (!sel || sel.selectedIndex < 0) { el.textContent = ''; return; } const opt = sel.options[sel.selectedIndex]; const fovW = parseFloat(opt.dataset.fovw), fovH = parseFloat(opt.dataset.fovh); if (!isFinite(fovW) || !isFinite(fovH)) { el.textContent = ''; return; } let text = `FOV (Rig): ${Math.round(fovW)}′ × ${Math.round(fovH)}′`; if (typeof OBJECT_SIZE_ARCMIN === 'number' && isFinite(OBJECT_SIZE_ARCMIN) && OBJECT_SIZE_ARCMIN > 0) { const minSide = Math.min(fovW, fovH), fitAcross = minSide / OBJECT_SIZE_ARCMIN; text += ` • Object ~ ${Math.round(OBJECT_SIZE_ARCMIN)}′ → ${fitAcross >= 1 ? 'fits' : 'spans'} ${fitAcross.toFixed(1)}× ${fitAcross >= 1 ? 'across' : 'of'} short side`; } el.textContent = text; }
(function recenterViewWhenLocked() { const canvas = document.getElementById('aladin-lite-div'); if (!canvas) return; canvas.addEventListener('mouseup', () => { if (lockToObject && objectCoords) aladin.gotoObject([objectCoords.ra, objectCoords.dec]); }); })();
function pad(n, w = 2) { return n.toString().padStart(w, '0'); }
function formatRA(raDeg) { const totalSec = raDeg / 15 * 3600, h = Math.floor(totalSec / 3600), m = Math.floor((totalSec % 3600) / 60), s = (totalSec % 60).toFixed(2); return `${pad(h)}:${pad(m)}:${pad(s, 5)}`; }
function formatDec(decDeg) { const sign = decDeg >= 0 ? '+' : '-', abs = Math.abs(decDeg), d = Math.floor(abs), m = Math.floor((abs - d) * 60), s = ((abs - d) * 60 - m) * 60; return `${sign}${pad(d)}:${pad(m)}:${s.toFixed(1).padStart(4, '0')}`; }
function getFrameCenterRaDec() { if (lockToObject) { const rc = aladin.getRaDec(); return { ra: rc[0], dec: rc[1] }; } if (fovCenter && isFinite(fovCenter.ra) && isFinite(fovCenter.dec)) return fovCenter; const rc = aladin.getRaDec(); return { ra: rc[0], dec: rc[1] }; }
function parseRaDec(raHMS, decDMS) { try { const [h, m, s] = raHMS.split(':').map(parseFloat), ra = (h + m / 60 + s / 3600) * 15.0, sign = decDMS.trim()[0] === '-' ? -1 : 1, [d, dm, ds] = decDMS.replace('+', '').replace('-', '').split(':').map(parseFloat), dec = sign * (d + dm / 60 + ds / 3600); return {ra, dec}; } catch (e) { return null; } }
function formatDateISOtoEuropean(iso_str) { if (!iso_str || typeof iso_str !== 'string') return 'N/A'; const parts = iso_str.split("-"); if (parts.length !== 3) { console.warn("formatDateISOtoEuropean received unexpected format:", iso_str); return iso_str; } const [year, month, day] = parts; return `${day}.${month}.${year}`; }
function setLocation() { const selectedLocation = document.getElementById('location-select').value; fetch('/set_location', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({location: selectedLocation}) }).then(response => response.json()).then(data => { if (data.status === 'success') refreshChart(); else console.error("Location update failed:", data); }).catch(error => console.error('Error setting location:', error)); }
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
    .then(data => {
        alert(data.status === "success" ? "Project updated successfully!" : data.error);
    });
}

function copyFramingUrl() { try { const q = buildFramingQuery(), url = location.origin + location.pathname + q; navigator.clipboard.writeText(url); console.log("[Framing] Copied URL:", url); } catch (e) { console.warn("[Framing] copyFramingUrl failed:", e); } }
function loadImagingOpportunities() { document.getElementById("opportunities-section").style.display = "block"; const tbody = document.getElementById("opportunities-body"); tbody.innerHTML = `<tr><td colspan="9">Searching...</td></tr>`; const objectName = NOVA_GRAPH_DATA.objectName; fetch(`/get_imaging_opportunities/${encodeURIComponent(objectName)}`).then(response => response.json()).then(data => { if (data.status === "success") { if (data.results.length === 0) { tbody.innerHTML = `<tr><td colspan="9">No good dates found matching your criteria.</td></tr>`; return; } let htmlRows = ""; const selectedDateStr = `${document.getElementById('year-select').value.padStart(4, '0')}-${document.getElementById('month-select').value.padStart(2, '0')}-${document.getElementById('day-select').value.padStart(2, '0')}`, plotLat = NOVA_GRAPH_DATA.plotLat, plotLon = NOVA_GRAPH_DATA.plotLon; data.results.forEach(r => { const isSelected = r.date === selectedDateStr, formattedDate = formatDateISOtoEuropean(r.date), ics_url = `/generate_ics/${encodeURIComponent(objectName)}?date=${r.date}&tz=${encodeURIComponent(plotTz)}&lat=${plotLat}&lon=${plotLon}&max_alt=${r.max_alt}&moon_illum=${r.moon_illumination}&obs_dur=${r.obs_minutes}&from_time=${r.from_time}&to_time=${r.to_time}`, filename = `imaging_${objectName.replace(/\s+/g, '_')}_${r.date}.ics`; htmlRows += `<tr class="${isSelected ? 'highlight' : ''}" data-date="${r.date}" onclick="selectSuggestedDate('${r.date}')" style="cursor: pointer;"><td>${formattedDate}</td><td>${r.from_time}</td><td>${r.to_time}</td><td>${r.obs_minutes}</td><td>${r.max_alt}</td><td>${r.moon_illumination}</td><td>${r.moon_separation}</td><td>${r.rating || ""}</td><td onclick="event.stopPropagation();"><a href="${ics_url}" download="${filename}" title="Add to calendar" style="font-size: 1.5em; text-decoration: none;">🗓️</a></td></tr>`; }); tbody.innerHTML = htmlRows; } else tbody.innerHTML = `<tr><td colspan="9">Error: ${data.message}</td></tr>`; }); }
function selectSuggestedDate(dateStr) { const [year, month, day] = dateStr.split('-').map(Number); document.getElementById('year-select').value = year; document.getElementById('month-select').value = month; document.getElementById('day-select').value = day; changeView('day'); setTimeout(() => { const rows = document.getElementById("opportunities-body").querySelectorAll("tr"); rows.forEach(row => { row.classList.toggle("highlight", row.getAttribute("data-date") === dateStr); }); }, 100); }
function openInStellarium() { document.getElementById('stellarium-status').textContent = "Sending object to Stellarium..."; document.getElementById('stellarium-status').style.color = "#666"; const objectName = NOVA_GRAPH_DATA.objectName; fetch("/proxy_focus", { method: "POST", headers: {"Content-Type": "application/x-www-form-urlencoded"}, body: new URLSearchParams({target: objectName, mode: "center"}) }).then(async response => { let data; try { data = await response.json(); } catch (e) { data = {message: "Could not parse server response."}; } if (response.ok && data.status === "success") { document.getElementById('stellarium-status').textContent = "Stellarium view updated!"; document.getElementById('stellarium-status').style.color = "#83b4c5"; } else document.getElementById('stellarium-status').innerHTML = `<p style="color:red; margin:0;">Error: ${data.message || "Unknown error"}</p>`; }); }
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
    const center = getFrameCenterRaDec();
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
        mosaic_overlap: parseFloat(document.getElementById('mosaic-overlap')?.value || 10)
    };

    // 2. Send to DB
    fetch('/api/save_framing', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    })
    .then(r => r.json())
    .then(data => {
        if(data.status === 'success') {
            alert("Framing settings saved to database.");
            checkAndShowFramingButton();
        } else {
            alert("Error saving: " + data.message);
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

function copyAsiairMosaic() {
    if (!aladin || !fovCenter) return;

    const cols = parseInt(document.getElementById('mosaic-cols')?.value || 1);
    const rows = parseInt(document.getElementById('mosaic-rows')?.value || 1);
    const overlapPercent = parseFloat(document.getElementById('mosaic-overlap')?.value || 0);
    const overlap = overlapPercent / 100;
    const rotDeg = parseFloat(document.getElementById('framing-rotation')?.value || 0);

    const fovRigSel = document.getElementById('framing-rig-select');
    if (!fovRigSel || fovRigSel.selectedIndex < 0) {
        alert("Please select a rig first.");
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

    const center = fovCenter;
    const ang = -rotDeg * Math.PI / 180; // Standard math (CCW) vs Screen (CW)
    const ra0 = center.ra * Math.PI / 180;
    const dec0 = center.dec * Math.PI / 180;

    // Tangent plane vectors
    const cX = Math.cos(dec0) * Math.cos(ra0);
    const cY = Math.cos(dec0) * Math.sin(ra0);
    const cZ = Math.sin(dec0);
    const eX = -Math.sin(ra0), eY = Math.cos(ra0), eZ = 0;
    const nX = -Math.sin(dec0) * Math.cos(ra0), nY = -Math.sin(dec0) * Math.sin(ra0), nZ = Math.cos(dec0);

    let clipboardText = "";
    // Exact Telescopius Header
    clipboardText += "Pane, RA, DEC, Position Angle (East), Pane width (arcmins), Pane height (arcmins), Overlap, Row, Column\n";

    // Prepare Base Name
    const rotInt = Math.round((rotDeg % 360 + 360) % 360);
    const safeName = (NOVA_GRAPH_DATA.objectName || "Target").replace(/\s+/g, '_');

    let paneCount = 1;

    for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
            // ... Grid Logic ...
            const cx_off = (c - (cols - 1) / 2.0) * wStep;
            const cy_off = (r - (rows - 1) / 2.0) * hStep;
            const rx = cx_off * Math.cos(ang) - cy_off * Math.sin(ang);
            const ry = cx_off * Math.sin(ang) + cy_off * Math.cos(ang);
            const dx = (rx * Math.PI / 180);
            const dy = (ry * Math.PI / 180);
            const rad = Math.hypot(dx, dy);
            let paneRa = center.ra;
            let paneDec = center.dec;
            if (rad > 1e-9) {
                const sinC = Math.sin(rad), cosC = Math.cos(rad);
                const dirX = (dx * eX + dy * nX) / rad;
                const dirY = (dx * eY + dy * nY) / rad;
                const dirZ = (dx * eZ + dy * nZ) / rad;
                const pX = cosC * cX + sinC * dirX;
                const pY = cosC * cY + sinC * dirY;
                const pZ = cosC * cZ + sinC * dirZ;
                let raRad = Math.atan2(pY, pX);
                if (raRad < 0) raRad += 2 * Math.PI;
                paneRa = raRad * 180 / Math.PI;
                paneDec = Math.asin(pZ) * 180 / Math.PI;
            }

            // Full Row Construction (Match spacing)
            const pName = `${safeName}_Rot${rotInt}_P${paneCount}`;
            const pRa = formatRaCsv(paneRa);
            const pDec = formatDecCsv(paneDec);
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

    navigator.clipboard.writeText(clipboardText).then(() => {
        alert(
            `Copied ${paneCount-1} pane(s) to clipboard (CSV Format).\n\n` +
            `• ASIAIR: Go to Plan > Import > Paste.\n` +
            `• N.I.N.A.: Save as .csv and import into Sequencer.\n\n` +
            `NOTE: Coordinates are J2000. Rotation is included.`
        );
    }).catch(err => {
        console.error('Clipboard write failed:', err);
        alert("Failed to copy to clipboard. See console.");
    });
}

function deleteSavedFraming() {
    const objectName = NOVA_GRAPH_DATA.objectName;
    if (!confirm("Are you sure you want to delete the saved framing for this object?")) return;

    fetch('/api/delete_framing', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ object_name: objectName })
    })
    .then(r => r.json())
    .then(data => {
        if(data.status === 'success') {
            // Clear the UI
            const container = document.getElementById('project-quick-link');
            if (container) container.innerHTML = '';
        } else {
            alert("Error deleting: " + data.message);
        }
    });
}

function checkAndShowFramingButton() {
    const objectName = NOVA_GRAPH_DATA.objectName;
    const container = document.getElementById('project-quick-link');
    if (!container) return;

    fetch(`/api/get_framing/${encodeURIComponent(objectName)}`)
        .then(r => r.json())
        .then(data => {
            container.innerHTML = '';

            if (data.status === 'found') {
                // Create a wrapper div to hold both buttons side-by-side
                const wrapper = document.createElement('div');
                wrapper.style.display = 'flex';
                wrapper.style.alignItems = 'center';
                wrapper.style.gap = '8px';

                // 1. OPEN BUTTON
                const btn = document.createElement('button');
                btn.className = 'inline-button';
                btn.textContent = 'Open Saved Framing';
                btn.style.fontSize = '13px';
                btn.style.padding = '6px 12px';

                btn.onclick = () => {
                    const params = new URLSearchParams();
                    if(data.rig) params.set('rig', data.rig);
                    if(data.ra) params.set('ra', data.ra);
                    if(data.dec) params.set('dec', data.dec);
                    params.set('rot', data.rotation);
                    if(data.survey) params.set('survey', data.survey);
                    if(data.blend) params.set('blend', data.blend);
                    params.set('blend_op', data.blend_op);

                    // Restore Mosaic
                    if(data.mosaic_cols) params.set('m_cols', data.mosaic_cols);
                    if(data.mosaic_rows) params.set('m_rows', data.mosaic_rows);
                    if(data.mosaic_overlap) params.set('m_ov', data.mosaic_overlap);

                    openFramingAssistant(params.toString());
                };

                // 2. DELETE BUTTON
                const delBtn = document.createElement('button');
                delBtn.className = 'inline-button';
                delBtn.textContent = 'Delete Saved Framing';
                delBtn.title = "Delete Saved Framing";
                delBtn.style.fontSize = '13px';
                delBtn.style.padding = '6px 10px';
                delBtn.style.backgroundColor = '#c0392b'; // Red background
                delBtn.style.color = 'white'; // White text

                delBtn.onclick = deleteSavedFraming;

                // Add both to wrapper, then wrapper to container
                wrapper.appendChild(btn);
                wrapper.appendChild(delBtn);
                container.appendChild(wrapper);
            }
        });
}

window.addEventListener('load', () => {
    if (window['chartjs-plugin-annotation']) Chart.register(window['chartjs-plugin-annotation']);

    changeView('day');

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
        .catch(e => {
            console.error("Trix upload failed", e);
            alert("Image upload failed. See console for details.");
            attachment.remove();
        });
    }

    // General UI
    const lockBox = document.getElementById('lock-to-object');
    if (lockBox) lockBox.checked = true;

    const q = new URLSearchParams(location.search);
    if (q.has('rig') && (q.has('ra') || q.has('dec'))) setTimeout(() => openFramingAssistant(), 0);

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

    // Modal Listeners
    const framingModal = document.getElementById('framing-modal');
    if (framingModal) {
        window.addEventListener('click', e => {
            const framingModal = document.getElementById('framing-modal');
            // Only close if the modal is actually visible and the click was on the dark backdrop
            if (framingModal && framingModal.style.display === 'block' && e.target === framingModal) {
                closeFramingAssistant();
            }
        });
    }
    window.addEventListener('resize', () => {
        if (document.getElementById('framing-modal').style.display === 'block') {
            if (typeof aladin !== 'undefined' && aladin) updateFramingChart(false);
        }
    });

    // --- CHECK DATABASE FOR FRAMING ---
    checkAndShowFramingButton();
});