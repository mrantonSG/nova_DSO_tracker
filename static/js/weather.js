/**
 * weather.js
 * ClearDarkSky-style weather forecast panel for Nova DSO Tracker.
 * Fetches hourly/daily astronomical weather data and renders an
 * interactive colour-coded grid inspired by ClearDarkSky charts.
 *
 * Public API (attached to window):
 *   initWeatherPanel()
 *   fetchHourlyWeather(lat, lon)
 *   fetchDailyWeather(lat, lon)
 *   renderHourlyGrid(apiResponse)
 *   renderDailyCards(apiResponse)
 *   getConditionClass(value, metric)
 *   formatHour(datetimeOrHour)
 *   isNightHour(hour)
 */
(function () {
    'use strict';

    // ================================================================
    // INJECT COMPONENT STYLES
    // Self-contained so the file works without a separate CSS import.
    // ================================================================
    (function injectStyles() {
        if (document.getElementById('weather-panel-styles')) return;
        const style = document.createElement('style');
        style.id = 'weather-panel-styles';
        style.textContent = `
            /* --- Toolbar ------------------------------------------- */
            .weather-toolbar {
                display: flex;
                align-items: center;
                gap: 12px;
                margin-bottom: 14px;
                flex-wrap: wrap;
            }
            .weather-view-toggle {
                display: flex;
                gap: 3px;
                background: var(--bg-light-gray, #f0f0f0);
                border-radius: 8px;
                padding: 3px;
            }
            .weather-view-btn {
                padding: 5px 14px;
                border: none;
                background: transparent;
                border-radius: 6px;
                cursor: pointer;
                font-size: 13px;
                color: var(--text-secondary, #4a4a4a);
                transition: background 0.15s, color 0.15s;
                font-family: inherit;
            }
            .weather-view-btn.active {
                background: var(--bg-white, #fff);
                color: var(--primary-color, #83b4c5);
                box-shadow: 0 1px 3px rgba(0,0,0,0.10);
                font-weight: 600;
            }
            :root[data-theme="dark"] .weather-view-toggle {
                background: var(--bg-dark-tertiary, #1c2230);
            }
            :root[data-theme="dark"] .weather-view-btn.active {
                background: var(--bg-dark-elevated, #1a1f28);
                color: var(--primary-light, #a8cdd8);
            }

            /* --- Grid scroll wrapper ------------------------------- */
            .weather-grid-scroll {
                overflow-x: auto;
                -webkit-overflow-scrolling: touch;
                border-radius: 6px;
            }
            .weather-grid-table {
                border-collapse: separate;
                border-spacing: 1px;
                min-width: 500px;
                background: var(--border-medium, #e5e2dc);
                border-radius: 6px;
                overflow: hidden;
            }

            /* --- Row label (sticky left column) -------------------- */
            .weather-label {
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 0.06em;
                text-transform: uppercase;
                color: var(--text-secondary, #4a4a4a);
                background: var(--bg-light-gray, #f0f0f0);
                padding: 4px 8px;
                white-space: nowrap;
                position: sticky;
                left: 0;
                z-index: 2;
                min-width: 64px;
            }
            :root[data-theme="dark"] .weather-label {
                background: var(--bg-dark-secondary, #161b24);
                color: rgba(255,255,255,0.50);
            }
            .weather-label .row-icon {
                margin-right: 4px;
                opacity: 0.65;
            }

            /* --- Time header row ----------------------------------- */
            .weather-row--time th.weather-label {
                background: #0a0d14;
            }
            .weather-cell--time {
                font-size: 10px;
                font-family: var(--font-mono, 'DM Mono', monospace);
                font-weight: 600;
                background: var(--bg-dark-primary, #0f1118);
                color: var(--text-muted, #888);
                padding: 4px 2px;
                text-align: center;
                white-space: nowrap;
            }
            .weather-cell--time.night-hour {
                background: #111827;
                color: var(--primary-light, #a8cdd8);
            }
            .weather-cell--date-label {
                font-size: 9px;
                font-family: var(--font-mono, 'DM Mono', monospace);
                background: #0a0d14;
                color: rgba(131,180,197,0.6);
                padding: 2px 2px;
                text-align: center;
                white-space: nowrap;
            }
            .weather-cell--date-label.night-hour {
                color: rgba(131,180,197,0.8);
            }

            /* --- Data cells ---------------------------------------- */
            .weather-cell {
                padding: 3px 2px;
                text-align: center;
                font-size: 10px;
                font-family: var(--font-mono, 'DM Mono', monospace);
                cursor: default;
                transition: filter 0.12s;
                min-width: 32px;
            }
            .weather-cell:hover {
                filter: brightness(1.18);
                position: relative;
                z-index: 1;
            }
            .cell-value {
                display: block;
                line-height: 1.35;
                user-select: none;
            }
            .cell-value.na {
                opacity: 0.35;
            }

            /* Night-start vertical accent */
            .night-start-border {
                border-left: 2px solid rgba(131,180,197,0.45) !important;
            }

            /* --- Condition colour scale: 1=best, 9=worst ----------- */
            .condition-1  { background: #145a32; color: #a9dfbf; }
            .condition-2  { background: #1e8449; color: #abebc6; }
            .condition-3  { background: #58b15e; color: #eafaf1; }
            .condition-4  { background: #93c847; color: #1a3a0d; }
            .condition-5  { background: #d4d629; color: #2a2a00; }
            .condition-6  { background: #e6960f; color: #2a1800; }
            .condition-7  { background: #d4550d; color: #fff0e0; }
            .condition-8  { background: #b92c0c; color: #ffe0d8; }
            .condition-9  { background: #7b0a0a; color: #ffd0d0; }
            .condition-na { background: transparent; color: var(--text-muted, #888); font-style: italic; }

            :root[data-theme="dark"] .condition-na {
                background: transparent;
                color: rgba(255,255,255,0.28);
                font-style: italic;
            }

            /* --- Loading state ------------------------------------- */
            .weather-loading[hidden],
            .weather-error[hidden] {
                display: none !important;
            }
            .weather-loading {
                display: flex;
                flex-direction: column;
                align-items: center;
                gap: 12px;
                padding: 48px 24px;
                color: var(--text-muted, #888);
                font-size: 13px;
            }
            .weather-spinner {
                width: 30px;
                height: 30px;
                border: 3px solid var(--border-light, #eee);
                border-top-color: var(--primary-color, #83b4c5);
                border-radius: 50%;
                animation: wx-spin 0.7s linear infinite;
            }
            @keyframes wx-spin { to { transform: rotate(360deg); } }

            /* --- Simulation mode notice ---------------------------- */
            .weather-sim-notice {
                display: flex;
                align-items: center;
                gap: 8px;
                padding: 8px 12px;
                margin-bottom: 12px;
                background: rgba(255, 193, 7, 0.12);
                border: 1px solid rgba(255, 193, 7, 0.3);
                border-radius: 6px;
                font-size: 12px;
                color: #b38f00;
            }
            :root[data-theme="dark"] .weather-sim-notice {
                background: rgba(255, 193, 7, 0.08);
                border-color: rgba(255, 193, 7, 0.25);
                color: #ffc107;
            }
            .weather-sim-notice[hidden] {
                display: none !important;
            }
            .weather-sim-notice svg {
                flex-shrink: 0;
                width: 16px;
                height: 16px;
            }

            /* --- Error state --------------------------------------- */
            .weather-error {
                display: flex;
                align-items: center;
                gap: 10px;
                padding: 16px 18px;
                background: var(--bg-light-gray, #f0f0f0);
                border-radius: 8px;
                color: var(--text-secondary, #4a4a4a);
                font-size: 13px;
            }
            :root[data-theme="dark"] .weather-error {
                background: var(--bg-dark-secondary, #161b24);
                color: rgba(255,255,255,0.55);
            }
            .weather-error .error-icon { font-size: 18px; opacity: 0.7; }

            /* --- Meta info bar ------------------------------------- */
            .weather-meta {
                margin-top: 8px;
                font-size: 10px;
                font-family: var(--font-mono, 'DM Mono', monospace);
                color: var(--text-light, #aaa);
            }

            /* --- Legend -------------------------------------------- */
            .weather-legend {
                display: flex;
                align-items: center;
                gap: 10px;
                margin-top: 10px;
                padding: 6px 10px;
                background: var(--bg-light-gray, #f0f0f0);
                border-radius: 5px;
                flex-wrap: wrap;
                font-size: 10px;
            }
            :root[data-theme="dark"] .weather-legend {
                background: var(--bg-dark-secondary, #161b24);
            }
            .legend-title {
                font-weight: 700;
                letter-spacing: 0.06em;
                text-transform: uppercase;
                color: var(--text-muted, #888);
            }
            .legend-scale {
                display: flex;
                gap: 2px;
                align-items: center;
            }
            .legend-scale span {
                display: inline-block;
                width: 15px;
                height: 15px;
                border-radius: 2px;
                text-align: center;
                font-size: 9px;
                line-height: 15px;
                font-family: var(--font-mono, monospace);
            }
            .legend-endpoints {
                display: flex;
                gap: 6px;
                color: var(--text-muted, #888);
            }

            /* --- Daily cards --------------------------------------- */
            .weather-daily-cards {
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
                gap: 10px;
                padding: 4px 0;
            }
            .weather-day-card {
                background: var(--bg-white, #fff);
                border: 1px solid var(--border-light, #eee);
                border-radius: 10px;
                padding: 12px;
                box-shadow: 0 1px 3px rgba(0,0,0,0.07);
                transition: box-shadow 0.18s;
            }
            .weather-day-card:hover {
                box-shadow: 0 3px 10px rgba(0,0,0,0.10);
            }
            :root[data-theme="dark"] .weather-day-card {
                background: var(--bg-dark-secondary, #161b24);
                border-color: var(--bg-dark-border, #2a2e38);
            }
            .day-card-header {
                display: flex;
                align-items: center;
                justify-content: space-between;
                margin-bottom: 8px;
                padding-bottom: 7px;
                border-bottom: 1px solid var(--border-light, #eee);
                font-size: 13px;
                font-weight: 600;
                color: var(--text-primary, #141414);
            }
            :root[data-theme="dark"] .day-card-header {
                color: rgba(255,255,255,0.90);
                border-color: var(--bg-dark-border, #2a2e38);
            }
            .day-overall-dot {
                display: inline-block;
                width: 10px;
                height: 10px;
                border-radius: 50%;
                flex-shrink: 0;
            }
            .day-card-metrics {
                display: flex;
                flex-direction: column;
                gap: 5px;
                margin-bottom: 8px;
            }
            .day-metric {
                display: flex;
                align-items: center;
                gap: 6px;
            }
            .metric-icon {
                font-size: 11px;
                width: 13px;
                text-align: center;
                opacity: 0.65;
                flex-shrink: 0;
            }
            .metric-bar {
                display: inline-block;
                width: 12px;
                height: 12px;
                border-radius: 2px;
                flex-shrink: 0;
            }
            .metric-label {
                font-size: 11px;
                color: var(--text-secondary, #4a4a4a);
            }
            :root[data-theme="dark"] .metric-label {
                color: rgba(255,255,255,0.55);
            }
            .day-card-footer {
                display: flex;
                flex-wrap: wrap;
                gap: 5px;
                padding-top: 7px;
                border-top: 1px solid var(--border-light, #eee);
                font-size: 10px;
                color: var(--text-muted, #888);
            }
            :root[data-theme="dark"] .day-card-footer {
                border-color: var(--bg-dark-border, #2a2e38);
            }
            .day-wind, .day-temp, .day-humid { white-space: nowrap; }
        `;
        document.head.appendChild(style);
    })();


    // ================================================================
    // MODULE STATE
    // ================================================================
    let currentView = 'hourly';   // 'hourly' | 'daily'
    let currentLat  = null;
    let currentLon  = null;


    // ================================================================
    // PUBLIC — initWeatherPanel()
    // ================================================================
    /**
     * Main entry point.  Call once when the Weather tab / panel is
     * added to the DOM.  Sets up location reading, view-toggle buttons,
     * and kicks off the first data load.
     */
    function initWeatherPanel() {
        _getLocationFromPage();
        _setupLocationListener();
        _setupViewToggle();
        _updateMoonInfo();
        _updateSimModeNotice();

        if (currentLat !== null && currentLon !== null) {
            _loadWeatherForCurrentView();
        } else {
            _showError('No location selected. Please choose a location to see the weather forecast.');
        }
    }

    function _updateSimModeNotice() {
        var panel = document.getElementById('weather-panel');
        if (!panel) return;

        var notice = panel.querySelector('.weather-sim-notice');
        var simToggle = document.getElementById('sim-mode-toggle');
        var isSimMode = simToggle && simToggle.checked;

        if (!notice) {
            notice = document.createElement('div');
            notice.className = 'weather-sim-notice';
            notice.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>' +
                '<span>Simulation mode active — showing current forecast (weather APIs do not support historical/future dates)</span>';
            var controls = panel.querySelector('.weather-controls');
            if (controls && controls.nextSibling) {
                controls.parentNode.insertBefore(notice, controls.nextSibling);
            } else {
                panel.insertBefore(notice, panel.firstChild);
            }
        }

        if (isSimMode) {
            notice.removeAttribute('hidden');
        } else {
            notice.setAttribute('hidden', '');
        }
    }


    // ================================================================
    // PUBLIC — fetchHourlyWeather(lat, lon)
    // ================================================================
    /**
     * Fetch hourly weather data from /api/v1/weather/hourly.
     * Results are cached in sessionStorage for 3 hours.
     * @param {number} lat
     * @param {number} lon
     * @returns {Promise<Object>}  Full API response
     */
    async function fetchHourlyWeather(lat, lon) {
        const cacheKey    = `nova_weather_hourly_${lat.toFixed(3)}_${lon.toFixed(3)}`;
        const CACHE_EXPIRY = 3 * 60 * 60 * 1000; // 3 h

        const cached = _checkCache(cacheKey, CACHE_EXPIRY);
        if (cached) return cached;

        const response = await fetch(`/api/v1/weather/hourly?lat=${lat}&lon=${lon}`);
        if (!response.ok) {
            const txt = await response.text().catch(() => '');
            throw new Error(`Weather API ${response.status}${txt ? ': ' + txt.substring(0, 120) : ''}`);
        }

        const json = await response.json();
        _saveCache(cacheKey, json);
        return json;
    }


    // ================================================================
    // PUBLIC — fetchDailyWeather(lat, lon)
    // ================================================================
    /**
     * Fetch daily weather data from /api/v1/weather/daily.
     * Results are cached in sessionStorage for 3 hours.
     * @param {number} lat
     * @param {number} lon
     * @returns {Promise<Object>}  Full API response
     */
    async function fetchDailyWeather(lat, lon) {
        const cacheKey    = `nova_weather_daily_${lat.toFixed(3)}_${lon.toFixed(3)}`;
        const CACHE_EXPIRY = 3 * 60 * 60 * 1000; // 3 h

        const cached = _checkCache(cacheKey, CACHE_EXPIRY);
        if (cached) return cached;

        const response = await fetch(`/api/v1/weather/daily?lat=${lat}&lon=${lon}`);
        if (!response.ok) {
            const txt = await response.text().catch(() => '');
            throw new Error(`Weather API ${response.status}${txt ? ': ' + txt.substring(0, 120) : ''}`);
        }

        const json = await response.json();
        _saveCache(cacheKey, json);
        return json;
    }


    // ================================================================
    // PUBLIC — renderHourlyGrid(apiResponse)
    // ================================================================
    /**
     * Render a ClearDarkSky-style hourly colour grid.
     *
     * Grid layout (rows × columns):
     *   Date   | 06.03 |       |       | 07.03 | ...
     *   Time   | 18:00 | 19:00 | 20:00 | 00:00 | ...
     *   Clouds |  ██   |  ▒▒   |  ░░   |  ○    | ...
     *   Seeing |   2   |   2   |   3   |  N/A  | ...
     *   Transp |   1   |   2   |   2   |   3   | ...
     *   Wind   |   3   |   4   |   4   |   5   | ...
     *   Humid. |  48%  |  51%  |  55%  |  60%  | ...
     *   Temp.  |  12°  |  11°  |  10°  |   9°  | ...
     *
     * @param {Object} apiResponse  Full API response { data: { dataseries, init }, meta }
     */
    function renderHourlyGrid(apiResponse) {
        const container = document.getElementById('weather-grid-container');
        if (!container) return;

        // API returns { data: [...], meta: { init, lat, lon, count } }
        const dataseries = apiResponse && apiResponse.data;
        const meta       = apiResponse && apiResponse.meta;
        const init       = meta && meta.init;  // "YYYYMMDDHH"

        if (!dataseries || !Array.isArray(dataseries) || dataseries.length === 0) {
            _showError('No hourly weather data available for this location.');
            return;
        }

        // --- Parse init timestamp ---
        let initDate = null;
        if (init && init.length >= 8) {
            const yr = parseInt(init.substring(0, 4), 10);
            const mo = parseInt(init.substring(4, 6), 10) - 1; // 0-based
            const dy = parseInt(init.substring(6, 8), 10);
            const hr = init.length >= 10 ? parseInt(init.substring(8, 10), 10) : 0;
            initDate = new Date(Date.UTC(yr, mo, dy, hr));
        }

        // --- Build column metadata (max 48 h) ---
        const MAX_COLS = 48;
        const columns = dataseries.slice(0, MAX_COLS).map(function (d) {
            let colDate = null;
            let hour    = 0;
            if (initDate) {
                colDate = new Date(initDate.getTime() + d.timepoint * 3600 * 1000);
                hour    = colDate.getUTCHours();
            }
            return {
                hour:    hour,
                label:   formatHour(hour),
                date:    colDate,
                isNight: isNightHour(hour),
                isNewDay: colDate ? colDate.getUTCHours() === 0 : false,
                data:    d
            };
        });

        // Helper: is this column the start of a night block?
        function isNightStart(i) {
            if (i === 0) return columns[0].isNight;
            return columns[i].isNight && !columns[i - 1].isNight;
        }

        // --- Build HTML ---
        let html = '<div class="weather-grid-scroll"><table class="weather-grid-table">';

        // Colgroup for fixed widths
        html += '<colgroup><col style="width:68px">';
        columns.forEach(function () { html += '<col style="min-width:32px;width:32px">'; });
        html += '</colgroup>';

        html += '<thead>';

        // Date row — shown only when the date changes
        const hasDayChange = columns.some(function (c) { return c.isNewDay; });
        if (hasDayChange || (columns[0] && columns[0].date)) {
            html += '<tr>';
            html += '<th class="weather-label" style="font-size:9px">Date</th>';
            columns.forEach(function (col, i) {
                const ns   = isNightStart(i) ? ' night-start-border' : '';
                const cls  = col.isNight ? ' night-hour' : '';
                const text = col.isNewDay && col.date ? _fmtShortDate(col.date) : '';
                html += `<th class="weather-cell--date-label${cls}${ns}">${text}</th>`;
            });
            html += '</tr>';
        }

        // Time row
        html += '<tr class="weather-row--time">';
        html += '<th class="weather-label">Time</th>';
        columns.forEach(function (col, i) {
            const ns  = isNightStart(i) ? ' night-start-border' : '';
            const cls = col.isNight ? ' night-hour' : '';
            const ttl = col.date
                ? `${_fmtShortDate(col.date)} ${col.label}`
                : col.label;
            html += `<th class="weather-cell--time${cls}${ns}" title="${ttl}">${col.label}</th>`;
        });
        html += '</tr></thead><tbody>';

        // Cloud Cover
        html += _buildRow('☁', 'Clouds', columns, function (d, i) {
            const val = d.cloudcover;
            const cls = getConditionClass(val, 'cloudcover');
            const ns  = isNightStart(i) ? ' night-start-border' : '';
            const lbl = _cloudCoverSymbol(val);
            const tip = `Cloud cover: ${_cloudCoverText(val)} (${val}/9)`;
            return `<td class="weather-cell ${cls}${ns}" title="${tip}"><span class="cell-value">${lbl}</span></td>`;
        });

        // Seeing
        html += _buildRow('★', 'Seeing', columns, function (d, i) {
            const val = d.seeing;
            const ns  = isNightStart(i) ? ' night-start-border' : '';
            if (val === -9999 || val === undefined || val === null) {
                return `<td class="weather-cell condition-na${ns}" title="Seeing: N/A"><span class="cell-value na">–</span></td>`;
            }
            const cls = getConditionClass(val, 'seeing');
            const tip = `Seeing: ${_seeingText(val)} (${val}/8)`;
            return `<td class="weather-cell ${cls}${ns}" title="${tip}"><span class="cell-value">${val}</span></td>`;
        });

        // Transparency
        html += _buildRow('◈', 'Transp.', columns, function (d, i) {
            const val = d.transparency;
            const ns  = isNightStart(i) ? ' night-start-border' : '';
            if (val === -9999 || val === undefined || val === null) {
                return `<td class="weather-cell condition-na${ns}" title="Transparency: N/A"><span class="cell-value na">–</span></td>`;
            }
            const cls = getConditionClass(val, 'transparency');
            const tip = `Transparency: ${_transparencyText(val)} (${val}/8)`;
            return `<td class="weather-cell ${cls}${ns}" title="${tip}"><span class="cell-value">${val}</span></td>`;
        });

        // Wind
        html += _buildRow('〜', 'Wind', columns, function (d, i) {
            const wind  = d.wind10m;
            const speed = wind
                ? (wind.speed !== undefined ? wind.speed : wind)
                : (d.wind_speed !== undefined ? d.wind_speed : null);
            const dir   = (wind && wind.direction) ? wind.direction : '';
            const ns    = isNightStart(i) ? ' night-start-border' : '';
            if (speed === null || speed === undefined) {
                return `<td class="weather-cell condition-na${ns}" title="Wind: N/A"><span class="cell-value na">–</span></td>`;
            }
            const cls = getConditionClass(speed, 'wind');
            const tip = `Wind: ${speed} m/s${dir ? ' ' + dir : ''}`;
            return `<td class="weather-cell ${cls}${ns}" title="${tip}"><span class="cell-value" style="font-size:9px">${speed}</span></td>`;
        });

        // Humidity
        html += _buildRow('💧', 'Humid.', columns, function (d, i) {
            const val = d.rh2m;
            const ns  = isNightStart(i) ? ' night-start-border' : '';
            if (val === null || val === undefined) {
                return `<td class="weather-cell condition-na${ns}" title="Humidity: N/A"><span class="cell-value na">–</span></td>`;
            }
            const cls = getConditionClass(val, 'humidity');
            const tip = `Humidity: ${val}%`;
            return `<td class="weather-cell ${cls}${ns}" title="${tip}"><span class="cell-value" style="font-size:9px">${val}%</span></td>`;
        });

        // Temperature (neutral — no quality mapping)
        html += _buildRow('°C', 'Temp.', columns, function (d, i) {
            const val = d.temp2m;
            const ns  = isNightStart(i) ? ' night-start-border' : '';
            const disp = (val !== null && val !== undefined) ? `${val}°` : '—';
            return `<td class="weather-cell${ns}" title="Temperature: ${disp}C" style="color:var(--text-secondary,#4a4a4a)"><span class="cell-value" style="font-size:9px">${disp}</span></td>`;
        });

        html += '</tbody></table></div>';

        // Meta info
        if (init) {
            const modelStr = (meta && meta.model) ? ` · Model: ${meta.model}` : '';
            html += `<div class="weather-meta">Forecast init: ${_fmtInitString(init)}${modelStr}</div>`;
        }

        // Legend
        html += _buildLegend();

        container.innerHTML = html;
        _hideLoading();
        _hideError();
    }


    // ================================================================
    // PUBLIC — renderDailyCards(apiResponse)
    // ================================================================
    /**
     * Render daily summary cards with colour-coded condition bars.
     * @param {Object} apiResponse  Full API response { data: [...], meta: { init, ... } }
     */
    function renderDailyCards(apiResponse) {
        const container = document.getElementById('weather-daily-list');
        if (!container) return;

        const dataseries = apiResponse && apiResponse.data;
        const meta       = apiResponse && apiResponse.meta;
        const init       = meta && meta.init;

        if (!dataseries || !Array.isArray(dataseries) || dataseries.length === 0) {
            _showError('No daily weather data available for this location.');
            return;
        }

        // Parse init date for timepoint-based date calculation
        let initDate = null;
        if (init && init.length >= 8) {
            const yr = parseInt(init.substring(0, 4), 10);
            const mo = parseInt(init.substring(4, 6), 10) - 1;
            const dy = parseInt(init.substring(6, 8), 10);
            const hr = init.length >= 10 ? parseInt(init.substring(8, 10), 10) : 0;
            initDate = new Date(Date.UTC(yr, mo, dy, hr));
        }

        let html = '<div class="weather-daily-cards">';

        dataseries.forEach(function (day) {
            let dateStr = 'Unknown';
            if (day.date) {
                dateStr = _fmtDateString(day.date);
            } else if (day.day_index !== undefined && initDate) {
                const d = new Date(initDate.getTime() + day.day_index * 24 * 3600 * 1000);
                dateStr = _fmtShortDate(d);
            } else if (day.timepoint !== undefined && initDate) {
                const d = new Date(initDate.getTime() + day.timepoint * 3600 * 1000);
                dateStr = _fmtShortDate(d);
            }

            // Cloud cover (daily API uses night_cloudcover_avg)
            const cloudVal   = day.night_cloudcover_avg !== undefined ? day.night_cloudcover_avg
                             : (day.cloudcover !== undefined ? day.cloudcover : day.cloud_cover);
            const cloudClass = getConditionClass(cloudVal, 'cloudcover');

            // Seeing (daily API uses night_seeing_avg)
            const seeingVal  = day.night_seeing_avg !== undefined ? day.night_seeing_avg : day.seeing;
            const seeingBad  = (seeingVal === -9999 || seeingVal === undefined || seeingVal === null);
            const seeingCls  = seeingBad ? 'condition-na' : getConditionClass(seeingVal, 'seeing');
            const seeingLbl  = seeingBad ? '—' : _seeingText(seeingVal);

            // Transparency (daily API uses night_transparency_avg)
            const transVal   = day.night_transparency_avg !== undefined ? day.night_transparency_avg : day.transparency;
            const transBad   = (transVal === -9999 || transVal === undefined || transVal === null);
            const transCls   = transBad ? 'condition-na' : getConditionClass(transVal, 'transparency');
            const transLbl   = transBad ? '—' : _transparencyText(transVal);

            // Wind (daily API may use wind_avg)
            const wind       = day.wind10m;
            const windSpeed  = day.wind_avg !== undefined ? day.wind_avg
                             : (wind ? (wind.speed !== undefined ? wind.speed : wind)
                             : (day.wind_speed !== undefined ? day.wind_speed : '?'));
            const windDir    = (wind && wind.direction) ? ` ${wind.direction}` : '';
            const windCls    = getConditionClass(windSpeed, 'wind');

            // Temp & humidity (daily API uses temp2m_avg, rh2m_avg)
            const temp    = day.temp2m_avg !== undefined ? day.temp2m_avg
                          : (day.temp2m !== undefined ? day.temp2m : (day.temperature !== undefined ? day.temperature : '?'));
            const humidity= day.rh2m_avg !== undefined ? day.rh2m_avg
                          : (day.rh2m !== undefined ? day.rh2m : (day.humidity !== undefined ? day.humidity : '?'));
            const humCls  = getConditionClass(humidity, 'humidity');

            // Overall quality dot
            const overallCls = _getOverallClass(cloudVal, seeingVal, transVal);

            html += `
                <div class="weather-day-card">
                    <div class="day-card-header">
                        <span class="day-date">${dateStr}</span>
                        <span class="day-overall-dot ${overallCls}" title="Overall: ${overallCls.replace('condition-', '')}/9"></span>
                    </div>
                    <div class="day-card-metrics">
                        <div class="day-metric">
                            <span class="metric-icon">☁</span>
                            <span class="metric-bar ${cloudClass}" title="${_cloudCoverText(cloudVal)}"></span>
                            <span class="metric-label">${_cloudCoverText(cloudVal)}</span>
                        </div>
                        <div class="day-metric">
                            <span class="metric-icon">★</span>
                            <span class="metric-bar ${seeingCls}" title="Seeing: ${seeingLbl}"></span>
                            <span class="metric-label">Seeing: ${seeingLbl}</span>
                        </div>
                        <div class="day-metric">
                            <span class="metric-icon">◈</span>
                            <span class="metric-bar ${transCls}" title="Transparency: ${transLbl}"></span>
                            <span class="metric-label">Transp.: ${transLbl}</span>
                        </div>
                        <div class="day-metric">
                            <span class="metric-icon">〜</span>
                            <span class="metric-bar ${windCls}" title="Wind: ${windSpeed}${windDir} m/s"></span>
                            <span class="metric-label">${windSpeed}${windDir} m/s</span>
                        </div>
                    </div>
                    <div class="day-card-footer">
                        <span class="day-temp">🌡 ${temp}°C</span>
                        <span class="day-humid">💧 ${humidity}%</span>
                    </div>
                </div>`;
        });

        html += '</div>';

        if (init) {
            const modelStr = (meta && meta.model) ? ` · Model: ${meta.model}` : '';
            html += `<div class="weather-meta">Forecast init: ${_fmtInitString(init)}${modelStr}</div>`;
        }

        html += _buildLegend();

        container.innerHTML = html;
        _hideLoading();
        _hideError();
    }


    // ================================================================
    // PUBLIC — getConditionClass(value, metric)
    // ================================================================
    /**
     * Map a numeric weather value to a CSS class string.
     *
     * Classes condition-1 … condition-9:
     *   1 = best (green)  →  9 = worst (dark red)
     *
     * Scale definitions:
     *   cloudcover   : 1 (clear) → 9 (overcast)
     *   seeing       : 1 (excellent) → 8 (very poor)   missing = -9999
     *   transparency : 1 (excellent) → 8 (terrible)    missing = -9999
     *   wind         : speed in m/s, lower = better
     *   humidity     : % relative humidity, lower = better for astronomy
     *
     * @param {number|null} value
     * @param {string}      metric  'cloudcover'|'seeing'|'transparency'|'wind'|'humidity'
     * @returns {string}  e.g. 'condition-3'
     */
    function getConditionClass(value, metric) {
        if (value === null || value === undefined || value === -9999) return 'condition-na';
        const v = parseFloat(value);
        if (isNaN(v)) return 'condition-na';

        switch (metric) {
            case 'cloudcover':
                // 1 = clear (best) → condition-1, 9 = overcast → condition-9
                return `condition-${Math.max(1, Math.min(9, Math.round(v)))}`;

            case 'seeing':
            case 'transparency':
                // 1 = excellent (best) → condition-1, 8 = terrible → condition-8
                return `condition-${Math.max(1, Math.min(8, Math.round(v)))}`;

            case 'wind':
                // m/s → quality class (calm = best)
                if (v <= 1)  return 'condition-1';
                if (v <= 3)  return 'condition-2';
                if (v <= 5)  return 'condition-3';
                if (v <= 7)  return 'condition-4';
                if (v <= 9)  return 'condition-5';
                if (v <= 12) return 'condition-6';
                if (v <= 15) return 'condition-7';
                if (v <= 19) return 'condition-8';
                return 'condition-9';

            case 'humidity':
                // % → quality class (dry = best)
                if (v <= 25) return 'condition-1';
                if (v <= 40) return 'condition-2';
                if (v <= 52) return 'condition-3';
                if (v <= 63) return 'condition-4';
                if (v <= 73) return 'condition-5';
                if (v <= 81) return 'condition-6';
                if (v <= 88) return 'condition-7';
                if (v <= 94) return 'condition-8';
                return 'condition-9';

            default:
                return '';
        }
    }


    // ================================================================
    // PUBLIC — formatHour(datetimeOrHour)
    // ================================================================
    /**
     * Format a datetime string or integer hour to "HH:00".
     *
     * Accepts:
     *   - Integer 0–23
     *   - String "2024-03-06 18:00:00"  → "18:00"
     *   - String "18:30"                → "18:00"
     *
     * @param {number|string} datetimeOrHour
     * @returns {string}  e.g. "18:00"
     */
    function formatHour(datetimeOrHour) {
        if (typeof datetimeOrHour === 'string') {
            const m = datetimeOrHour.match(/(\d{1,2}):\d{2}/);
            if (m) return `${String(parseInt(m[1], 10)).padStart(2, '0')}:00`;
            return datetimeOrHour.substring(0, 5);
        }
        const h = parseInt(datetimeOrHour, 10);
        if (isNaN(h)) return '--:--';
        return `${String(h).padStart(2, '0')}:00`;
    }


    // ================================================================
    // PUBLIC — isNightHour(hour)
    // ================================================================
    /**
     * Return true if the given hour falls inside the typical
     * astronomical observing window (18:00–06:00 local/UTC).
     *
     * @param {number|string} hour  Integer or string 0–23
     * @returns {boolean}
     */
    function isNightHour(hour) {
        const h = parseInt(hour, 10);
        if (isNaN(h)) return false;
        return h >= 18 || h < 6;
    }


    // ================================================================
    // PRIVATE — setup helpers
    // ================================================================

    function _setupLocationListener() {
        const sel = document.getElementById('location-select');
        if (!sel) return;
        sel.addEventListener('change', function () {
            _parseLocationFromSelect(this);
            if (currentLat !== null && currentLon !== null) {
                _loadWeatherForCurrentView();
            }
        });
    }

    function _setupViewToggle() {
        // Find all view buttons using data-weather-view-btn attribute
        const viewBtns = document.querySelectorAll('[data-weather-view-btn]');
        const hourlyView = document.getElementById('weather-hourly');
        const dailyView = document.getElementById('weather-daily');
        const satelliteView = document.getElementById('weather-satellite');

        viewBtns.forEach(function(btn) {
            btn.addEventListener('click', function() {
                const view = this.getAttribute('data-view');
                if (currentView === view) return;

                // Update button states
                viewBtns.forEach(function(b) {
                    b.classList.remove('active');
                    b.setAttribute('aria-selected', 'false');
                });
                this.classList.add('active');
                this.setAttribute('aria-selected', 'true');

                // Hide all views
                if (hourlyView) hourlyView.setAttribute('hidden', '');
                if (dailyView) dailyView.setAttribute('hidden', '');
                if (satelliteView) satelliteView.setAttribute('hidden', '');

                // Show selected view
                currentView = view;
                if (view === 'hourly' && hourlyView) {
                    hourlyView.removeAttribute('hidden');
                    _loadWeatherForCurrentView();
                } else if (view === 'daily' && dailyView) {
                    dailyView.removeAttribute('hidden');
                    _loadWeatherForCurrentView();
                } else if (view === 'satellite' && satelliteView) {
                    satelliteView.removeAttribute('hidden');
                    _loadSatelliteView();
                }
            });
        });

        // Setup refresh button
        const refreshBtn = document.querySelector('[data-weather-refresh]');
        if (refreshBtn) {
            refreshBtn.addEventListener('click', function() {
                // Clear cache for current location
                if (currentLat !== null && currentLon !== null) {
                    const hourlyKey = `nova_weather_hourly_${currentLat.toFixed(3)}_${currentLon.toFixed(3)}`;
                    const dailyKey = `nova_weather_daily_${currentLat.toFixed(3)}_${currentLon.toFixed(3)}`;
                    sessionStorage.removeItem(hourlyKey);
                    sessionStorage.removeItem(dailyKey);
                }
                if (currentView === 'satellite') {
                    _loadSatelliteView();
                } else {
                    _loadWeatherForCurrentView();
                }
            });
        }
    }

    function _loadSatelliteView() {
        const iframe = document.getElementById('satellite-frame');
        if (!iframe) return;
        if (currentLat === null || currentLon === null) {
            _showError('No location selected for satellite view.');
            return;
        }
        // Windy embed URL with current location
        const url = `https://embed.windy.com/embed2.html?lat=${currentLat}&lon=${currentLon}&detailLat=${currentLat}&detailLon=${currentLon}&zoom=6&level=surface&overlay=clouds&product=ecmwf&menu=&message=true&marker=&calendar=now&pressure=&type=map&location=coordinates&detail=&metricWind=km%2Fh&metricTemp=%C2%B0C&radarRange=-1`;
        iframe.src = url;
    }

    function _getLocationFromPage() {
        const sel = document.getElementById('location-select');
        if (sel) {
            _parseLocationFromSelect(sel);
            if (currentLat !== null && currentLon !== null) return;
        }
        // Fallback: dashboard stores selected location in sessionStorage
        const stored = sessionStorage.getItem('selectedLocation');
        if (stored) _parseLocationString(stored);
    }

    function _parseLocationFromSelect(select) {
        const opt = select.options[select.selectedIndex];
        if (!opt) return;

        // Prefer data-lat / data-lon attributes
        const latAttr = opt.getAttribute('data-lat');
        const lonAttr = opt.getAttribute('data-lon');
        if (latAttr && lonAttr) {
            const lat = parseFloat(latAttr);
            const lon = parseFloat(lonAttr);
            if (!isNaN(lat) && !isNaN(lon)) {
                currentLat = lat;
                currentLon = lon;
                return;
            }
        }

        // Fallback: value like "lat,lon"
        _parseLocationString(select.value);
    }

    function _parseLocationString(str) {
        if (!str) return;
        const parts = str.split(',');
        if (parts.length >= 2) {
            const lat = parseFloat(parts[0]);
            const lon = parseFloat(parts[1]);
            if (!isNaN(lat) && !isNaN(lon)) {
                currentLat = lat;
                currentLon = lon;
            }
        }
    }

    async function _loadWeatherForCurrentView() {
        if (currentLat === null || currentLon === null) {
            _showError('No location coordinates available. Please select a location.');
            return;
        }
        _showLoading();
        try {
            if (currentView === 'hourly') {
                const data = await fetchHourlyWeather(currentLat, currentLon);
                renderHourlyGrid(data);
            } else {
                const data = await fetchDailyWeather(currentLat, currentLon);
                renderDailyCards(data);
            }
        } catch (err) {
            console.error('[WeatherPanel]', err);
            _showError(`Failed to load weather data: ${err.message}`);
        }
    }

    function _showLoading() {
        const loading = document.getElementById('weather-loading');
        const error   = document.getElementById('weather-error');
        if (loading) loading.removeAttribute('hidden');
        if (error)   error.setAttribute('hidden', '');
    }

    function _hideLoading() {
        const loading = document.getElementById('weather-loading');
        if (loading) loading.setAttribute('hidden', '');
    }

    function _showError(message) {
        const loading = document.getElementById('weather-loading');
        const error   = document.getElementById('weather-error');
        const msgEl   = error && error.querySelector('[data-weather-error-msg]');
        if (loading)  loading.setAttribute('hidden', '');
        if (error)    error.removeAttribute('hidden');
        if (msgEl)    msgEl.textContent = message;
    }

    function _hideError() {
        const error = document.getElementById('weather-error');
        if (error) error.setAttribute('hidden', '');
    }


    // ================================================================
    // PRIVATE — grid / card rendering helpers
    // ================================================================

    /**
     * Build one <tr> for the hourly grid.
     * @param {string}   icon     Emoji / character for the label column
     * @param {string}   label    Text label
     * @param {Array}    columns  Column metadata array
     * @param {Function} cellFn   (data, index) → <td> HTML string
     */
    function _buildRow(icon, label, columns, cellFn) {
        let html = '<tr>';
        html += `<td class="weather-label"><span class="row-icon">${icon}</span>${label}</td>`;
        columns.forEach(function (col, i) {
            html += cellFn(col.data, i);
        });
        html += '</tr>';
        return html;
    }

    /** Unicode block characters representing cloud density */
    function _cloudCoverSymbol(val) {
        if (val === null || val === undefined) return '?';
        const v = parseInt(val, 10);
        if (v <= 1) return '○';
        if (v <= 2) return '░';
        if (v <= 3) return '░░';
        if (v <= 4) return '▒';
        if (v <= 5) return '▒▒';
        if (v <= 6) return '▓';
        if (v <= 7) return '▓▓';
        return '██';
    }

    function _cloudCoverText(val) {
        if (val === null || val === undefined) return 'Unknown';
        const labels = {
            1: 'Clear', 2: 'Mostly Clear', 3: 'Partly Cloudy',
            4: 'Partly Cloudy', 5: 'Mostly Cloudy', 6: 'Mostly Cloudy',
            7: 'Overcast', 8: 'Very Cloudy', 9: 'Overcast'
        };
        return labels[parseInt(val, 10)] || `${val}/9`;
    }

    function _seeingText(val) {
        if (val === null || val === undefined || val === -9999) return 'N/A';
        const labels = {
            1: 'Excellent', 2: 'Excellent', 3: 'Very Good', 4: 'Good',
            5: 'Average', 6: 'Below Avg', 7: 'Poor', 8: 'Very Poor'
        };
        return labels[parseInt(val, 10)] || `${val}/8`;
    }

    function _transparencyText(val) {
        if (val === null || val === undefined || val === -9999) return 'N/A';
        const labels = {
            1: 'Excellent', 2: 'Very Good', 3: 'Good', 4: 'Fair',
            5: 'Below Avg', 6: 'Poor', 7: 'Very Poor', 8: 'Terrible'
        };
        return labels[parseInt(val, 10)] || `${val}/8`;
    }

    /** Compute an overall quality class from the key night-sky metrics */
    function _getOverallClass(cloudcover, seeing, transparency) {
        const vals = [];
        if (cloudcover   !== undefined && cloudcover   !== null) vals.push(cloudcover);
        if (seeing        !== undefined && seeing        !== null && seeing       !== -9999) vals.push(seeing);
        if (transparency  !== undefined && transparency  !== null && transparency !== -9999) vals.push(transparency);
        if (vals.length === 0) return 'condition-na';
        const avg = vals.reduce(function (a, b) { return a + b; }, 0) / vals.length;
        return `condition-${Math.max(1, Math.min(9, Math.round(avg)))}`;
    }

    // ================================================================
    // PRIVATE — _updateMoonInfo()
    // ================================================================
    /**
     * Calculate the current moon phase client-side (no API needed) and
     * update the moon info panel DOM elements.
     *
     * Algorithm: simple synodic month formula anchored to J2000 epoch.
     *   phase fraction = ((days since 2000-01-01) % 29.53059) / 29.53059
     *
     * DOM targets:
     *   #weather-moon-phase        — phase name string
     *   #weather-moon-illumination — illumination percentage
     *   #weather-moon-rise         — estimated moonrise (~HH:00)
     *   #weather-moon-set          — estimated moonset  (~HH:00)
     *   #weather-moon-glyph        — unicode moon emoji
     */
    function _updateMoonInfo() {
        // Days elapsed since J2000.0 (2000-01-01 00:00 UTC)
        var j2000       = Date.UTC(2000, 0, 1);
        var daysSince   = (Date.now() - j2000) / 86400000;

        // Synodic month length
        var SYNODIC     = 29.53059;
        var cyclePos    = ((daysSince % SYNODIC) + SYNODIC) % SYNODIC; // 0 → SYNODIC
        var phaseNorm   = cyclePos / SYNODIC;                           // 0.0 → 1.0

        // Map phase fraction to name and glyph
        var phaseName, phaseGlyph;
        if (phaseNorm < 0.03 || phaseNorm >= 0.97) {
            phaseName  = 'New Moon';        phaseGlyph = '🌑';
        } else if (phaseNorm < 0.22) {
            phaseName  = 'Waxing Crescent'; phaseGlyph = '🌒';
        } else if (phaseNorm < 0.28) {
            phaseName  = 'First Quarter';   phaseGlyph = '🌓';
        } else if (phaseNorm < 0.47) {
            phaseName  = 'Waxing Gibbous';  phaseGlyph = '🌔';
        } else if (phaseNorm < 0.53) {
            phaseName  = 'Full Moon';       phaseGlyph = '🌕';
        } else if (phaseNorm < 0.72) {
            phaseName  = 'Waning Gibbous';  phaseGlyph = '🌖';
        } else if (phaseNorm < 0.78) {
            phaseName  = 'Last Quarter';    phaseGlyph = '🌗';
        } else {
            phaseName  = 'Waning Crescent'; phaseGlyph = '🌘';
        }

        // Illumination %: standard formula (0% new moon → 100% full moon)
        var illumination = Math.round((1 - Math.cos(phaseNorm * 2 * Math.PI)) / 2 * 100);

        // Estimated moonrise / moonset (linear offset from solar times):
        //   New moon  (phase 0.00): rise ~06:00, set ~18:00
        //   1st qtr   (phase 0.25): rise ~12:00, set ~00:00
        //   Full moon (phase 0.50): rise ~18:00, set ~06:00
        //   Last qtr  (phase 0.75): rise ~00:00, set ~12:00
        var riseH   = (6 + phaseNorm * 24) % 24;
        var setH    = (riseH + 12) % 24;
        var padTwo  = function (n) { return String(Math.floor(n)).padStart(2, '0'); };
        var moonRise = '~' + padTwo(riseH) + ':00';
        var moonSet  = '~' + padTwo(setH)  + ':00';

        var el;
        el = document.getElementById('weather-moon-phase');
        if (el) el.textContent = phaseName;

        el = document.getElementById('weather-moon-illumination');
        if (el) el.textContent = illumination + '%';

        el = document.getElementById('weather-moon-rise');
        if (el) el.textContent = moonRise;

        el = document.getElementById('weather-moon-set');
        if (el) el.textContent = moonSet;

        el = document.getElementById('weather-moon-glyph');
        if (el) el.textContent = phaseGlyph;
    }


    /** "DD.MM" from a UTC Date object */
    function _fmtShortDate(dateObj) {
        const d = String(dateObj.getUTCDate()).padStart(2, '0');
        const m = String(dateObj.getUTCMonth() + 1).padStart(2, '0');
        return `${d}.${m}`;
    }

    /** "DD.MM.YYYY" from "YYYY-MM-DD" */
    function _fmtDateString(dateStr) {
        if (!dateStr || typeof dateStr !== 'string') return 'Unknown';
        const p = dateStr.split('-');
        if (p.length === 3) return `${p[2]}.${p[1]}.${p[0]}`;
        return dateStr;
    }

    /** "2024-03-06 18:00 UTC" from "2024030618" */
    function _fmtInitString(init) {
        if (!init || init.length < 8) return init;
        const hr = init.length >= 10 ? init.substring(8, 10) : '00';
        return `${init.substring(0, 4)}-${init.substring(4, 6)}-${init.substring(6, 8)} ${hr}:00 UTC`;
    }

    function _buildLegend() {
        const steps = [1, 2, 3, 4, 5, 6, 7, 8, 9];
        const tips  = ['Excellent', 'Very Good', 'Good', 'Fair', 'Average',
                       'Below Avg', 'Poor', 'Very Poor', 'Terrible'];
        let scaleHtml = steps.map(function (n, i) {
            return `<span class="condition-${n}" title="${tips[i]}">&nbsp;</span>`;
        }).join('');
        return `
            <div class="weather-legend">
                <span class="legend-title">Conditions:</span>
                <div class="legend-scale">${scaleHtml}</div>
                <span class="legend-endpoints">
                    <span>Excellent</span>
                    <span>Poor</span>
                </span>
            </div>`;
    }


    // ================================================================
    // PRIVATE — cache helpers
    // ================================================================

    function _checkCache(key, expiryMs) {
        try {
            const raw = sessionStorage.getItem(key);
            if (raw) {
                const parsed = JSON.parse(raw);
                if (Date.now() - parsed.timestamp < expiryMs) return parsed.data;
            }
        } catch (e) {
            console.warn('[WeatherPanel] cache read:', e);
        }
        return null;
    }

    function _saveCache(key, data) {
        try {
            sessionStorage.setItem(key, JSON.stringify({ timestamp: Date.now(), data: data }));
        } catch (e) {
            console.warn('[WeatherPanel] cache write (storage full?):', e);
        }
    }


    // ================================================================
    // SIMULATION MODE LISTENERS
    // Refresh weather panel when simulation date changes
    // ================================================================
    (function _hookSimulationChanges() {
        function attachSimListeners() {
            var simToggle = document.getElementById('sim-mode-toggle');
            var simDate   = document.getElementById('sim-date-input');

            function refreshIfWeatherVisible() {
                var weatherWrapper = document.getElementById('weather-tab-content');
                if (weatherWrapper && weatherWrapper.style.display !== 'none') {
                    // Clear weather cache to force re-fetch
                    Object.keys(sessionStorage).forEach(function(key) {
                        if (key.indexOf('nova_weather_') === 0) {
                            sessionStorage.removeItem(key);
                        }
                    });
                    initWeatherPanel();
                }
            }

            if (simToggle && !simToggle._weatherListenerAttached) {
                simToggle.addEventListener('change', function() {
                    simToggle._weatherListenerAttached = true;
                    refreshIfWeatherVisible();
                });
                simToggle._weatherListenerAttached = true;
            }

            if (simDate && !simDate._weatherListenerAttached) {
                simDate.addEventListener('change', function() {
                    var toggle = document.getElementById('sim-mode-toggle');
                    if (toggle && toggle.checked) {
                        refreshIfWeatherVisible();
                    }
                });
                simDate._weatherListenerAttached = true;
            }
        }

        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', attachSimListeners);
        } else {
            attachSimListeners();
        }
    })();

    window.initWeatherPanel   = initWeatherPanel;
    window.fetchHourlyWeather = fetchHourlyWeather;
    window.fetchDailyWeather  = fetchDailyWeather;
    window.renderHourlyGrid   = renderHourlyGrid;
    window.renderDailyCards   = renderDailyCards;
    window.getConditionClass  = getConditionClass;
    window.formatHour         = formatHour;
    window.isNightHour        = isNightHour;
})();