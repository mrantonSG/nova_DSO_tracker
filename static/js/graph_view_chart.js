
let currentTimeUpdateInterval = null;
const weatherOverlayPlugin = {
  id: 'weatherOverlay',
  beforeDatasetsDraw(chart) {
    const info = chart.__weather;
    if (!info || !info.hasWeather || !Array.isArray(info.forecast) || info.forecast.length === 0) return;
    const { forecast: originalForecast, cloudInfo, seeingInfo } = info;

    // 1. Check if ANY block has valid seeing data (not null/undefined/-9999)
    const hasValidSeeingData = originalForecast.some(b =>
        b.seeing != null && b.seeing !== -9999
    );

    // 2. Group hourly forecast into 3-hour blocks for display
    const groupedForecast = [];
    if (originalForecast.length > 0) {
        // Sort just in case, though it should be sorted by time already
        originalForecast.sort((a, b) => a.start - b.start);

        let currentGroupStart = originalForecast[0].start;
        let groupEnd = currentGroupStart + (3 * 60 * 60 * 1000); // 3 hours in ms
        let blocksInGroup = [];

        for (const block of originalForecast) {
            // If block starts after the current group ends, finalize the previous group
            if (block.start >= groupEnd) {
                if (blocksInGroup.length > 0) {
                    // Find the most frequent cloudcover value (mode) in the group
                    const cloudCounts = blocksInGroup.reduce((acc, b) => {
                        acc[b.cloudcover] = (acc[b.cloudcover] || 0) + 1;
                        return acc;
                    }, {});
                    const dominantCloudcover = Object.keys(cloudCounts).reduce((a, b) => cloudCounts[a] > cloudCounts[b] ? a : b);

                    // Add the grouped block
                    groupedForecast.push({
                        start: currentGroupStart,
                        end: groupEnd,
                        cloudcover: parseInt(dominantCloudcover), // Ensure it's a number
                        // We don't average seeing - the row visibility handles it
                    });
                }
                // Start a new group
                currentGroupStart = block.start;
                // Ensure group boundaries align nicely if possible, but handle gaps
                const hoursSinceEpoch = Math.floor(block.start / (60 * 60 * 1000));
                const startHourOfBlock = hoursSinceEpoch % 24;
                const startHourOfGroup = Math.floor(startHourOfBlock / 3) * 3;
                // Recalculate group start/end based on 3-hour intervals (0, 3, 6, ...)
                const baseTime = new Date(block.start).setUTCHours(0,0,0,0); // Get start of day in UTC ms
                currentGroupStart = baseTime + startHourOfGroup * 3600 * 1000;
                groupEnd = currentGroupStart + (3 * 60 * 60 * 1000);
                blocksInGroup = [];
            }
             // Add block to the current group if it starts within the time window
             if (block.start >= currentGroupStart && block.start < groupEnd) {
                blocksInGroup.push(block);
             }
        }

        // Add the last group if it has blocks
        if (blocksInGroup.length > 0) {
            const cloudCounts = blocksInGroup.reduce((acc, b) => {
                acc[b.cloudcover] = (acc[b.cloudcover] || 0) + 1;
                return acc;
            }, {});
            const dominantCloudcover = Object.keys(cloudCounts).reduce((a, b) => cloudCounts[a] > cloudCounts[b] ? a : b);
            groupedForecast.push({
                start: currentGroupStart,
                end: groupEnd, // Use the calculated end
                cloudcover: parseInt(dominantCloudcover),
            });
        }
    }
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

    // Helper to get contrasting text color
    const getTextColorForBackground = (rgbaColor) => {
        if (!rgbaColor || !rgbaColor.startsWith('rgba')) return '#333';
        try {
            const [r, g, b] = rgbaColor.match(/\d+/g).map(Number);
            // Calculate perceived brightness (luminance)
            const luminance = (0.299 * r + 0.587 * g + 0.114 * b);
            // Return white for dark backgrounds, dark grey for light ones
            return luminance < 128 ? '#FFFFFF' : '#333';
        } catch (e) {
            return '#333'; // Fallback
        }
    };

    // Text fitting helper
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
      ctx.fillStyle = fill;
      ctx.fillRect(left + pad, y + pad, width - 2 * pad, rowH - 2 * pad);
      ctx.strokeStyle = 'rgba(0,0,0,0.1)';
      ctx.lineWidth = 1;
      ctx.strokeRect(left + pad + 0.5, y + pad + 0.5, width - 2 * pad - 1, rowH - 2 * pad - 1);

      const maxTextW = Math.max(0, width - 8);
      const txt = fitText(label, maxTextW);

      // Use the helper to set the text color dynamically
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
              // IMPORTANT: Since we grouped cloud cover, 'b.seeing' might not be relevant
              // for the whole block. We'll just draw a placeholder label or ideally
              // you'd modify the grouping logic to also determine a representative 'seeing'.
              // For simplicity now, let's just draw the row without text if we don't have
              // a representative value easily. Or find the middle block's seeing value.

              // Let's find an original block roughly in the middle of this group
              const midTime = b.start + (1.5 * 60 * 60 * 1000); // Middle of the 3hr block
              const originalBlockNearMid = originalForecast.find(ob => ob.start <= midTime && ob.end > midTime);
              const seeingValueToShow = originalBlockNearMid ? originalBlockNearMid.seeing : null;

              if (seeingValueToShow != null && seeingValueToShow !== -9999) {
                  const si = seeingInfo[seeingValueToShow] || { label: 'Seeing?', color: 'rgba(0,0,0,0.08)' };
                  drawBlock(topY + rowH + gap, x0, x1, si.label, si.color);
              } else {
                  // Optional: Draw an empty grey block if no representative seeing found for this specific group
                   drawBlock(topY + rowH + gap, x0, x1, '', 'rgba(0,0,0,0.08)');
              }
          }
    });
    ctx.restore();
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

function getDateTimeMs(baseDateISO, timeStr) {
    if (!timeStr || !timeStr.includes(':')) return null;
    const [hour, minute] = timeStr.split(':').map(Number);
    const base = luxon.DateTime.fromISO(baseDateISO, {zone: plotTz}).startOf('day');
    return base.set({hour, minute, second: 0, millisecond: 0}).toMillis();
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
        const hasWeather = Array.isArray(data.weather_forecast) && data.weather_forecast.length > 0;
        // Normalize weather block times to ms (Chart time scale handles multiple types, but we ensure consistency)
        const forecastForOverlay = hasWeather ? data.weather_forecast.map(b => ({
            start: toMs(b.start),
            end: toMs(b.end),
            cloudcover: b.cloudcover,
            seeing: b.seeing
        })) : [];
        // Filter to blocks that actually have drawable start/end values
        const drawableForecast = forecastForOverlay.filter(b => Number.isFinite(b.start) && Number.isFinite(b.end) && b.end > b.start);
        const hasDrawableWeather = drawableForecast.length > 0;
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
            plugins: hasDrawableWeather ? [nightShade, weatherOverlayPlugin] : [nightShade],
            options: {
                responsive: true,
                maintainAspectRatio: true,
                aspectRatio: 2,
                adapters: {date: {zone: plotTz}},
                layout: { padding: { top: hasDrawableWeather ? 46 : 0 } },
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
        window.altitudeChart.__weather = { hasWeather: hasDrawableWeather, forecast: drawableForecast, cloudInfo, seeingInfo };
        startCurrentTimeUpdater(window.altitudeChart);
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
    const v = Math.max(-90, Math.min(90, Number(val) || 0));
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
function setProjectQuickLink(url) {
    const container = document.getElementById('project-quick-link');
    if (!container) return;
    container.innerHTML = '';
    if (url) {
        const btn = document.createElement('button');
        btn.className = 'inline-button';
        btn.style.fontSize = '13px';
        btn.style.padding = '6px 12px';
        btn.textContent = 'Re-open Last Saved Framing';
        btn.onclick = () => {
            try {
                const u = new URL(url, location.origin);
                const qNow = new URLSearchParams(buildFramingQuery().slice(1));
                qNow.forEach((v, k) => u.searchParams.set(k, v));
                history.pushState(null, '', u.pathname + '?' + u.searchParams.toString());
            } catch (e) { history.pushState(null, '', url); }
            openFramingAssistant();
        };
        container.appendChild(btn);
    }
}
function openFramingAssistant() {
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

    (function ensureRotationReadout(){ const slider = document.getElementById('framing-rotation'); if (!slider) return; slider.setAttribute('min', '-90'); slider.setAttribute('max', '90'); slider.setAttribute('step', '0.5'); if (!slider.hasAttribute('value')) slider.setAttribute('value', '0'); let n = slider.nextSibling; while (n && n.nodeType === Node.TEXT_NODE) { const t = n.textContent.trim(), next = n.nextSibling; if (t === '' || t === '0°') n.parentNode.removeChild(n); else break; n = next; } const existingSpans = Array.from(document.querySelectorAll('#rotation-value')); let span = existingSpans[0]; if (existingSpans.length > 1) existingSpans.slice(1).forEach(el => el.remove()); if (!span) { span = document.createElement('span'); span.id = 'rotation-value'; span.style.marginLeft = '8px'; span.style.fontWeight = 'normal'; span.style.fontSize = '15px'; slider.insertAdjacentElement('afterend', span); try { span.style.fontWeight = 'normal'; } catch(_) {} } try { span.style.cursor = 'pointer'; span.title = 'Tap to reset rotation to 0°'; span.addEventListener('click', () => { const slider = document.getElementById('framing-rotation'); if (!slider) return; slider.value = '0'; slider.dispatchEvent(new Event('input', { bubbles: true })); }, { once: false }); } catch (e) {} })();
    if (!aladin) {
        aladin = A.aladin('#aladin-lite-div', { survey: "P/DSS2/color", fov: 1.5, cooFrame: 'ICRS', showFullscreenControl: false, showGotoControl: false });
        (function installSlowWheelZoom(){ if (window.__novaSlowZoomInstalled) return; const host = document.getElementById('aladin-lite-div'); if (!host) return; try { host.style.overscrollBehavior = 'contain'; } catch(e) {} function onWheel(ev) { if (ev.ctrlKey) return; ev.preventDefault(); ev.stopPropagation(); if (!aladin) return; const unit = (ev.deltaMode === 1) ? 16 : (ev.deltaMode === 2) ? 400 : 1; let dy = (ev.deltaY || 0) * unit; dy = Math.max(-80, Math.min(80, dy)); const g = aladin.getFov(), current = Array.isArray(g) ? (g[0] ?? 1) : (g ?? 1); const scale = Math.exp(dy * 0.00075), minFov = 0.01, maxFov = 180; const next = Math.min(maxFov, Math.max(minFov, current * scale)); if (Number.isFinite(next)) aladin.setFov(next); } host.addEventListener('wheel', onWheel, { passive: false, capture: true }); const tryBindCanvas = () => { const cv = host.querySelector('canvas'); if (cv) cv.addEventListener('wheel', onWheel, { passive: false, capture: true }); }; tryBindCanvas(); setTimeout(tryBindCanvas, 50); window.__novaSlowZoomInstalled = true; })();
        baseSurvey = aladin.getBaseImageLayer();
        let __blendSurveyId = null;
        function ensureBlendLayer() { if (!aladin) return null; const sel = document.getElementById('blend-survey-select'); if (!sel) return null; const surveyId = sel.value; const existing = aladin.getOverlayImageLayer && aladin.getOverlayImageLayer('blend'); if (existing && __blendSurveyId === surveyId) return existing; try { const hpx = (aladin.newImageSurvey) ? aladin.newImageSurvey(surveyId) : aladin.createImageSurvey(surveyId, surveyId, surveyId, 'equatorial', 9, { imgFormat: 'jpeg' }); if (hpx) { aladin.setOverlayImageLayer(hpx, 'blend'); __blendSurveyId = surveyId; return aladin.getOverlayImageLayer('blend'); } } catch (e) { console.warn('[nova] Could not create/set overlay image survey:', e); } return null; }
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
        window.addEventListener('keydown', (e) => { const k = e.key.toLowerCase(); if (['arrowup', 'arrowdown', 'arrowleft', 'arrowright', 'w', 'a', 's', 'd'].includes(k)) { e.preventDefault(); if (k === 'arrowup' || k === 'w') nudgeFov(0, +1); if (k === 'arrowdown' || k === 's') nudgeFov(0, -1); if (k === 'arrowleft' || k === 'a') nudgeFov(-1, 0); if (k === 'arrowright' || k === 'd') nudgeFov(+1, 0); } });
        (function wireRotationLiveUpdate(){ const rotInput = document.getElementById('framing-rotation'); if (!rotInput) return; const handler = () => { const raw = (typeof rotInput.valueAsNumber === 'number') ? rotInput.valueAsNumber : parseFloat(rotInput.value) || 0; const snapped = (Math.abs(raw) <= 1) ? 0 : raw; if (snapped !== raw) rotInput.value = String(snapped); updateFramingChart(false); }; try { rotInput.removeEventListener('input', rotInput.__novaRotHandler); } catch(e) {} try { rotInput.removeEventListener('change', rotInput.__novaRotHandler); } catch(e) {} rotInput.__novaRotHandler = handler; rotInput.addEventListener('input', handler); rotInput.addEventListener('change', handler); try { rotInput.setAttribute('value', String(rotInput.valueAsNumber ?? rotInput.value ?? 0)); } catch(e) {} handler(); })();
        (function wireInsertIntoProject(){ const btn = document.getElementById('insert-into-project'); if (!btn) return; try { btn.removeEventListener('click', btn.__novaInsertHandler); } catch(e) {} btn.__novaInsertHandler = (ev) => { try { const q = buildFramingQuery(), href = location.pathname + q; history.replaceState(null, '', href); } catch(e) { console.warn('[nova] Insert-to-project wiring error:', e); } }; btn.addEventListener('click', btn.__novaInsertHandler); })();
    }
    function buildFramingQuery() { const sel = document.getElementById('framing-rig-select'), rig = sel && sel.selectedIndex >= 0 ? sel.options[sel.selectedIndex].value : '', rotInput = document.getElementById('framing-rotation'), rot = rotInput ? (parseFloat(rotInput.value) || 0) : 0, sSel = document.getElementById('survey-select'), survey = sSel ? sSel.value : '', bSel = document.getElementById('blend-survey-select'), bOp = document.getElementById('blend-opacity'), blend = bSel ? bSel.value : '', blend_op = bOp ? (parseFloat(bOp.value) || 0) : 0; const { ra, dec } = (fovCenter || (aladin && (() => { const rc = aladin.getRaDec(); return { ra: rc[0], dec: rc[1] }; })()) || { ra: NaN, dec: NaN }); const qp = new URLSearchParams(); if (rig) qp.set('rig', rig); if (Number.isFinite(ra)) qp.set('ra', ra.toFixed(6)); if (Number.isFinite(dec)) qp.set('dec', dec.toFixed(6)); qp.set('rot', String(Math.round(to360(rot)))); if (survey) qp.set('survey', survey); if (blend) qp.set('blend', blend); qp.set('blend_op', String(Math.max(0, Math.min(1, blend_op)))); return '?' + qp.toString(); }
    let haveCenter = false, haveRot = false, haveRigRestored = false;
    try {
        const q = new URLSearchParams(location.search), rig = q.get('rig'), ra = parseFloat(q.get('ra')), dec = parseFloat(q.get('dec')), rot = parseFloat(q.get('rot')), surv = q.get('survey'), blend = q.get('blend'), blendOp = parseFloat(q.get('blend_op'));
        if (rig) { const sel = document.getElementById('framing-rig-select'); if (sel) { const idx = Array.from(sel.options).findIndex(o => o.value === rig); if (idx >= 0) { sel.selectedIndex = idx; haveRigRestored = true; } } }
        if (!Number.isNaN(rot)) { const rotInput = document.getElementById('framing-rotation'), signed = toSigned180(rot); if (rotInput) rotInput.value = signed; const rotSpan = document.getElementById('rotation-value'); if (rotSpan) rotSpan.textContent = `${Math.round(signed)}°`; haveRot = true; }
        if (surv) { if (typeof setSurvey === 'function') setSurvey(surv); else { const s = document.getElementById('survey-select'); if (s) s.value = surv; } }
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
    if (!haveCenter) applyLockToObject(true);
}
function closeFramingAssistant() { document.getElementById('framing-modal').style.display = 'none'; }
function flipFraming90() { const slider = document.getElementById('framing-rotation'); let v = parseFloat(slider.value) || 0; v += 90; if (v > 90) v = -90; slider.value = v; slider.dispatchEvent(new Event('input', { bubbles: true })); updateFramingChart(false); if (typeof updateReadoutFromCenter === 'function') updateReadoutFromCenter(); }
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
    (function updateRotationBadge(){ const el = document.getElementById('rotation-value'), sliderEl = document.getElementById('framing-rotation'), txt = `${Math.round(toSigned180(rotation))}°`; if (el) el.textContent = txt; if (sliderEl) sliderEl.title = `Rotation: ${txt}`; })();
    if (recenter) applyRigFovZoom(fovWidthArcmin, fovHeightArcmin, rotation);
    if (recenter) {
        aladin.gotoObject(objectName, {
            success: () => { applyRigFovZoom(fovWidthArcmin, fovHeightArcmin, rotation); const rc = aladin.getRaDec(); objectCoords = {ra: rc[0], dec: rc[1]}; fovCenter = lockToObject ? {...objectCoords} : {ra: rc[0], dec: rc[1]}; if (lockToObject) { if (fovLayer) fovLayer.removeAll(); updateScreenFovOverlay(fovWidthArcmin, fovHeightArcmin, rotation); } else drawFovFootprint(fovWidthArcmin, fovHeightArcmin, rotation, fovCenter); updateReadoutFromCenter?.(); },
            error: () => { const rc = aladin.getRaDec(); fovCenter = {ra: rc[0], dec: rc[1]}; if (lockToObject) { if (fovLayer) fovLayer.removeAll(); updateScreenFovOverlay(fovWidthArcmin, fovHeightArcmin, rotation); } else drawFovFootprint(fovWidthArcmin, fovHeightArcmin, rotation, fovCenter); updateReadoutFromCenter?.(); }
        }); return;
    }
    if (!fovCenter) { const rc = aladin.getRaDec(); fovCenter = {ra: rc[0], dec: rc[1]}; }
    if (lockToObject) { if (fovLayer) fovLayer.removeAll(); updateScreenFovOverlay(fovWidthArcmin, fovHeightArcmin, rotation); }
    else drawFovFootprint(fovWidthArcmin, fovHeightArcmin, rotation, fovCenter);
};
function drawFovFootprint(fovWidthArcmin, fovHeightArcmin, rotationDeg, center) {
    if (!aladin || !fovLayer || !center) return;
    fovLayer.removeAll();
    const halfW = (fovWidthArcmin / 60) / 2, halfH = (fovHeightArcmin / 60) / 2, ang = rotationDeg * Math.PI / 180;
    const ra0 = center.ra * Math.PI / 180, dec0 = center.dec * Math.PI / 180;
    const cX = Math.cos(dec0) * Math.cos(ra0), cY = Math.cos(dec0) * Math.sin(ra0), cZ = Math.sin(dec0);
    const eX = -Math.sin(ra0), eY = Math.cos(ra0), eZ = 0;
    const nX = -Math.sin(dec0) * Math.cos(ra0), nY = -Math.sin(dec0) * Math.sin(ra0), nZ = Math.cos(dec0);
    function rot2d(x, y) { return [x * Math.cos(ang) - y * Math.sin(ang), x * Math.sin(ang) + y * Math.cos(ang)]; }
    const raw = [[-halfW, -halfH], [halfW, -halfH], [halfW, halfH], [-halfW, halfH]].map(([x, y]) => rot2d(x, y));
    function planeToSky(x_deg, y_deg) {
        const dx = x_deg * Math.PI / 180, dy = y_deg * Math.PI / 180, r = Math.hypot(dx, dy);
        if (r < 1e-12) return [center.ra, center.dec];
        const dirX = (dx * eX + dy * nX) / r, dirY = (dx * eY + dy * nY) / r, dirZ = (dx * eZ + dy * nZ) / r;
        const s = Math.sin(r), c = Math.cos(r);
        const pX = c * cX + s * dirX, pY = c * cY + s * dirY, pZ = c * cZ + s * dirZ;
        let ra = Math.atan2(pY, pX); if (ra < 0) ra += 2 * Math.PI; const dec = Math.asin(pZ);
        return [ra * 180 / Math.PI, dec * 180 / Math.PI];
    }
    const polyCoords = raw.map(([x, y]) => planeToSky(x, y));
    polyCoords.push(polyCoords[0]);
    const fovPolygon = A.polygon(polyCoords, {color: '#83b4c5', lineWidth: 3});
    const fovFootprint = A.footprint(fovPolygon);
    fovLayer.add(fovFootprint);
    updateReadoutFromCenter?.();
}
let lockFovEnabled = false, lockRafId = null;
function updateScreenFovOverlay(fovWidthArcmin, fovHeightArcmin, rotationDeg) {
        const host = document.getElementById('aladin-lite-div');
        const rectEl = document.getElementById('screen-fov-rect');
        if (!host || !rectEl) return;

        const wpx = host.clientWidth || 1;
        const hpx = host.clientHeight || 1;

        const gf = aladin.getFov();
        const viewWdeg = Array.isArray(gf) ? (gf[0] ?? 1) : (gf ?? 1);

        // This is the corrected logic
        const viewHdeg = viewWdeg * (hpx / wpx); // Calculate the view's height in degrees
        const fovWdeg = (parseFloat(fovWidthArcmin) || 0) / 60;
        const fovHdeg = (parseFloat(fovHeightArcmin) || 0) / 60;
        if (!(fovWdeg > 0 && fovHdeg > 0)) return;

        const pxW = Math.max(2, (fovWdeg / viewWdeg) * wpx);
        const pxH = Math.max(2, (fovHdeg / viewHdeg) * hpx); // <-- THIS LINE IS FIXED

        rectEl.style.display = 'block';
        rectEl.style.width = pxW + 'px';
        rectEl.style.height = pxH + 'px';
        rectEl.style.marginLeft = (-pxW / 2) + 'px';
        rectEl.style.marginTop = (-pxH / 2) + 'px';
        rectEl.style.transform = `translate(0,0) rotate(${rotationDeg || 0}deg)`;
    }
function startLockOverlayLoop() { if (lockRafId) return; const tick = () => { if (!lockToObject) { lockRafId = null; return; } const sel = document.getElementById('framing-rig-select'), rot = parseFloat(document.getElementById('framing-rotation')?.value || '0') || 0; if (sel && sel.selectedIndex >= 0) { const opt = sel.options[sel.selectedIndex]; updateScreenFovOverlay(opt.dataset.fovw, opt.dataset.fovh, rot); } updateReadoutFromCenter(); lockRafId = requestAnimationFrame(tick); }; lockRafId = requestAnimationFrame(tick); }
function stopLockOverlayLoop() { if (lockRafId) { cancelAnimationFrame(lockRafId); lockRafId = null; } }
function setSurvey(hipsId) { if (!aladin) return; const newLayer = aladin.newImageSurvey(hipsId); aladin.setBaseImageLayer(newLayer); baseSurvey = aladin.getBaseImageLayer(); updateImageAdjustments(); }
function updateImageAdjustments() { if (!baseSurvey) return; const b = parseFloat(document.getElementById('img-bright').value), c = parseFloat(document.getElementById('img-contrast').value), g = parseFloat(document.getElementById('img-gamma').value), s = parseFloat(document.getElementById('img-sat').value); baseSurvey.setBrightness(b); baseSurvey.setContrast(c); baseSurvey.setGamma(g); baseSurvey.setSaturation(s); }
function updateReadout(raDeg, decDeg) { document.getElementById('ra-readout').value = formatRA(raDeg); document.getElementById('dec-readout').value = formatDec(decDeg); }
function updateReadoutFromCenter() { let center; if (lockToObject) { const rc = aladin.getRaDec(); center = { ra: rc[0], dec: rc[1] }; } else if (fovCenter && isFinite(fovCenter.ra) && isFinite(fovCenter.dec)) center = fovCenter; else { const rc = aladin.getRaDec(); center = { ra: rc[0], dec: rc[1] }; } updateReadout(center.ra, center.dec); }
function copyRaDec() { const text = `${document.getElementById('ra-readout').value} ${document.getElementById('dec-readout').value}`; navigator.clipboard.writeText(text); }
function changeView(view) {
    const day = document.getElementById('day-select').value, month = document.getElementById('month-select').value, year = document.getElementById('year-select').value, objectName = NOVA_GRAPH_DATA.objectName;
    fetch(`/get_date_info/${encodeURIComponent(objectName)}?day=${day}&month=${month}&year=${year}`).then(response => response.json()).then(data => { document.getElementById("phase-display").innerText = data.phase + "%"; document.getElementById("dusk-display").innerText = data.astronomical_dusk; document.getElementById("dawn-display").innerText = data.astronomical_dawn; });
    if (view === 'day') renderClientSideChart();
    else renderMonthlyYearlyChart(view);
}
function useReadoutAsFovCenter() { const raStr = document.getElementById('ra-readout').value, decStr = document.getElementById('dec-readout').value, sky = parseRaDec(raStr, decStr); if (!sky) return; fovCenter = {ra: sky.ra, dec: sky.dec}; updateFramingChart(false); updateReadoutFromCenter(); }
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
function saveProject() { const newProject = document.getElementById('project-field').value, objectName = NOVA_GRAPH_DATA.objectName; fetch('/update_project', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({object: objectName, project: newProject}) }).then(res => res.json()).then(data => { alert(data.status === "success" ? "Project updated successfully!" : data.error); }); }
function insertFramingIntoProject() { try { const ta = document.getElementById('project-field'); if (!ta) { alert('Project notes box not found.'); return; } const sel = document.getElementById('framing-rig-select'), rigId = sel?.value || '', rigName = (sel && sel.selectedIndex >= 0) ? sel.options[sel.selectedIndex].textContent.trim() : 'rig?', rotInput = document.getElementById('framing-rotation'), rotDeg = Math.round(parseFloat(rotInput?.value ?? '0')) || 0, survSel = document.getElementById('survey-select'), survey = survSel?.value || '', blendSel = document.getElementById('blend-survey-select'), blendSurvey = blendSel?.value || '', blendOpEl = document.getElementById('blend-opacity'), blendOp = Math.max(0, Math.min(1, parseFloat(blendOpEl?.value ?? '0') || 0)); const center = getFrameCenterRaDec(), raDeg = center.ra, decDeg = center.dec; updateReadout(raDeg, decDeg); const qs = new URLSearchParams(); if (rigId) qs.set('rig', rigId); if (Number.isFinite(raDeg)) qs.set('ra', raDeg.toFixed(6)); if (Number.isFinite(decDeg)) qs.set('dec', decDeg.toFixed(6)); qs.set('rot', String(rotDeg)); if (survey) qs.set('survey', survey); if (blendSurvey) qs.set('blend', blendSurvey); qs.set('blend_op', String(blendOp)); const url = `${location.origin}${location.pathname}?${qs.toString()}`; const objectName = NOVA_GRAPH_DATA.objectName, centerTxt = `RA ${formatRA(raDeg)}, Dec ${formatDec(decDeg)}`, line = `[Framing:${objectName}] ${rigName}, rot ${rotDeg}\u00B0, ${centerTxt}, survey ${survey || 'default'}${blendSurvey ? ` + blend ${blendSurvey} @ ${blendOp}` : ''}`; const lines = ta.value.split(/\r?\n/), out = []; for (let i = 0; i < lines.length; i++) { const L = lines[i]; if (/^\[Framing:/.test(L)) { if (L.startsWith(`[Framing:${objectName}]`)) { const maybeUrl = lines[i + 1] || ''; if (/^https?:\/\//i.test(maybeUrl)) i++; continue; } } out.push(L); } if (out.length && out[out.length - 1] !== '') out.push(''); out.push(line); out.push(url); ta.value = out.join('\n'); ta.dispatchEvent(new Event('input', { bubbles: true })); ta.scrollTop = ta.scrollHeight; setProjectQuickLink(url); } catch (e) { console.error('insertFramingIntoProject failed', e); alert('Could not insert framing into Project. See console for details.'); } }
function copyFramingUrl() { try { const q = buildFramingQuery(), url = location.origin + location.pathname + q; navigator.clipboard.writeText(url); console.log("[Framing] Copied URL:", url); } catch (e) { console.warn("[Framing] copyFramingUrl failed:", e); } }
function loadImagingOpportunities() { document.getElementById("opportunities-section").style.display = "block"; const tbody = document.getElementById("opportunities-body"); tbody.innerHTML = `<tr><td colspan="9">Searching...</td></tr>`; const objectName = NOVA_GRAPH_DATA.objectName; fetch(`/get_imaging_opportunities/${encodeURIComponent(objectName)}`).then(response => response.json()).then(data => { if (data.status === "success") { if (data.results.length === 0) { tbody.innerHTML = `<tr><td colspan="9">No good dates found matching your criteria.</td></tr>`; return; } let htmlRows = ""; const selectedDateStr = `${document.getElementById('year-select').value.padStart(4, '0')}-${document.getElementById('month-select').value.padStart(2, '0')}-${document.getElementById('day-select').value.padStart(2, '0')}`, plotLat = NOVA_GRAPH_DATA.plotLat, plotLon = NOVA_GRAPH_DATA.plotLon; data.results.forEach(r => { const isSelected = r.date === selectedDateStr, formattedDate = formatDateISOtoEuropean(r.date), ics_url = `/generate_ics/${encodeURIComponent(objectName)}?date=${r.date}&tz=${encodeURIComponent(plotTz)}&lat=${plotLat}&lon=${plotLon}&max_alt=${r.max_alt}&moon_illum=${r.moon_illumination}&obs_dur=${r.obs_minutes}&from_time=${r.from_time}&to_time=${r.to_time}`, filename = `imaging_${objectName.replace(/\s+/g, '_')}_${r.date}.ics`; htmlRows += `<tr class="${isSelected ? 'highlight' : ''}" data-date="${r.date}" onclick="selectSuggestedDate('${r.date}')" style="cursor: pointer;"><td>${formattedDate}</td><td>${r.from_time}</td><td>${r.to_time}</td><td>${r.obs_minutes}</td><td>${r.max_alt}</td><td>${r.moon_illumination}</td><td>${r.moon_separation}</td><td>${r.rating || ""}</td><td onclick="event.stopPropagation();"><a href="${ics_url}" download="${filename}" title="Add to calendar" style="font-size: 1.5em; text-decoration: none;">🗓️</a></td></tr>`; }); tbody.innerHTML = htmlRows; } else tbody.innerHTML = `<tr><td colspan="9">Error: ${data.message}</td></tr>`; }); }
function selectSuggestedDate(dateStr) { const [year, month, day] = dateStr.split('-').map(Number); document.getElementById('year-select').value = year; document.getElementById('month-select').value = month; document.getElementById('day-select').value = day; changeView('day'); setTimeout(() => { const rows = document.getElementById("opportunities-body").querySelectorAll("tr"); rows.forEach(row => { row.classList.toggle("highlight", row.getAttribute("data-date") === dateStr); }); }, 100); }
function openInStellarium() { document.getElementById('stellarium-status').textContent = "Sending object to Stellarium..."; document.getElementById('stellarium-status').style.color = "#666"; const objectName = NOVA_GRAPH_DATA.objectName; fetch("/proxy_focus", { method: "POST", headers: {"Content-Type": "application/x-www-form-urlencoded"}, body: new URLSearchParams({target: objectName, mode: "center"}) }).then(async response => { let data; try { data = await response.json(); } catch (e) { data = {message: "Could not parse server response."}; } if (response.ok && data.status === "success") { document.getElementById('stellarium-status').textContent = "Stellarium view updated!"; document.getElementById('stellarium-status').style.color = "#83b4c5"; } else document.getElementById('stellarium-status').innerHTML = `<p style="color:red; margin:0;">Error: ${data.message || "Unknown error"}</p>`; }); }

window.addEventListener('load', () => {
    if (window['chartjs-plugin-annotation']) Chart.register(window['chartjs-plugin-annotation']);
    const savedTab = localStorage.getItem('activeGraphTab') || 'chart';

    changeView('day');
    const lockBox = document.getElementById('lock-to-object');
    if (lockBox) lockBox.checked = true;
    const q = new URLSearchParams(location.search);
    if (q.has('rig') && (q.has('ra') || q.has('dec'))) setTimeout(() => openFramingAssistant(), 0);
    const dateEl = document.getElementById("date-display");
    // if (dateEl && dateEl.innerText.includes("-")) dateEl.innerText = formatDateISOtoEuropean(dateEl.innerText);
    const dayInput = document.getElementById("day-select"), monthSelect = document.getElementById("month-select"), yearInput = document.getElementById("year-select");
    function updateDayLimit() { const year = parseInt(yearInput.value), month = parseInt(monthSelect.value), daysInMonth = new Date(year, month, 0).getDate(); if (parseInt(dayInput.value) > daysInMonth) dayInput.value = daysInMonth; dayInput.max = daysInMonth; }
    monthSelect.addEventListener("change", updateDayLimit);
    yearInput.addEventListener("change", updateDayLimit);
    updateDayLimit();
    const framingModal = document.getElementById('framing-modal');
    window.addEventListener('click', function (event) { if (event.target == framingModal) closeFramingAssistant(); });
window.addEventListener('resize', () => {
    if (document.getElementById('framing-modal').style.display === 'block') {
        if (aladin) updateFramingChart(false);
    }
    // Chart.js will re-render the plugin overlay on resize automatically
});
    const ta = document.getElementById('project-field');
    if (ta) { const m = ta.value.match(/\bhttps?:\/\/[^\s<>"']+/g); if (m && m.length) setProjectQuickLink(m[m.length - 1]); }
});

function startCurrentTimeUpdater(chartInstance) {
    if (!chartInstance) return; // Don't run if the chart doesn't exist

    // Clear any previous timer first
    if (currentTimeUpdateInterval) {
        clearInterval(currentTimeUpdateInterval);
    }

    const updateLine = () => {
        // Check if the chart still exists before trying to update it
        if (!chartInstance || !chartInstance.options || !chartInstance.options.plugins?.annotation?.annotations) {
            if (currentTimeUpdateInterval) clearInterval(currentTimeUpdateInterval);
            currentTimeUpdateInterval = null;
            return;
        }

        // Get the current time in the chart's timezone
        const nowMs = luxon.DateTime.now().setZone(plotTz).toMillis();
        const annotations = chartInstance.options.plugins.annotation.annotations;

        // Update the position of our line
        if (annotations.currentTimeLine) {
            annotations.currentTimeLine.xMin = nowMs;
            annotations.currentTimeLine.xMax = nowMs;
            chartInstance.update('none'); // Update the chart without a flashy animation
        }
    };

    // Run it once immediately, and then set it to run every 10 minutes
    updateLine();
    currentTimeUpdateInterval = setInterval(updateLine, 1 * 60 * 1000); // 1 minutes in milliseconds
}