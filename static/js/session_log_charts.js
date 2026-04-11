/**
 * session_log_charts.js - Chart.js visualizations for session log analysis
 *
 * Provides 4 analysis tabs: Overview, Guiding, Dithering, AutoFocus
 * Uses Chart.js with dark theme support via stylingUtils
 */
(function() {
    'use strict';

    // Guard against double initialization
    if (window.sessionLogChartsInitialized) return;
    window.sessionLogChartsInitialized = true;

    // --- Color Scheme (Brand palette - matches base.css) ---
    // NOTE: COLORS are for data elements (lines, bars). Text/grid colors use getThemeColors().
    const COLORS = {
        ra: 'rgba(131, 180, 197, 0.85)',     // Primary teal 85% opacity
        dec: '#d4899e',                       // Soft rose (muted pink)
        total: '#9b8ec4',                     // Muted purple
        success: '#5eb570',                   // Soft chart green (not message green)
        timeout: '#e09090',                   // Soft coral/salmon (not harsh red)
        exposures: '#83b4c5',                 // Brand primary teal
        dither: '#d4899e',                    // Soft rose (matches dec)
        af: '#ffc107',                        // Brand warning amber
        meridianFlip: '#5eb570',              // Soft chart green
    };

    /**
     * Get theme-aware colors for chart UI elements (text, grid, tooltips).
     * Uses stylingUtils CSS variables with fallbacks for WCAG-compliant contrast.
     * @returns {Object} Theme colors: { text, textMuted, grid, tooltipBg, tooltipTitle, tooltipBody, tooltipBorder }
     */
    function getThemeColors() {
        const dark = isDarkTheme();

        // Use stylingUtils if available for CSS variable integration
        if (window.stylingUtils) {
            return {
                text: dark
                    ? window.stylingUtils.getThemeFallback('TEXT_PRIMARY')
                    : window.stylingUtils.getThemeFallback('TEXT_PRIMARY'),
                textMuted: dark ? '#b0b0b0' : '#666666',
                grid: dark
                    ? 'rgba(180, 180, 180, 0.25)'
                    : 'rgba(0, 0, 0, 0.1)',
                tooltipBg: dark
                    ? 'rgba(40, 40, 40, 0.95)'
                    : 'rgba(255, 255, 255, 0.92)',
                tooltipTitle: dark ? '#f0f0f0' : '#222222',
                tooltipBody: dark ? '#dddddd' : '#444444',
                tooltipBorder: dark
                    ? 'rgba(120, 120, 120, 0.5)'
                    : 'rgba(0, 0, 0, 0.15)'
            };
        }

        // Fallback without stylingUtils
        return {
            text: dark ? '#e0e0e0' : '#333333',
            textMuted: dark ? '#b0b0b0' : '#666666',
            grid: dark ? 'rgba(180, 180, 180, 0.25)' : 'rgba(0, 0, 0, 0.1)',
            tooltipBg: dark ? 'rgba(40, 40, 40, 0.95)' : 'rgba(255, 255, 255, 0.92)',
            tooltipTitle: dark ? '#f0f0f0' : '#222222',
            tooltipBody: dark ? '#dddddd' : '#444444',
            tooltipBorder: dark ? 'rgba(120, 120, 120, 0.5)' : 'rgba(0, 0, 0, 0.15)'
        };
    }

    // --- Chart instances for cleanup ---
    let charts = {
        guiding: null,
        dither: null,
        afCurves: [],
        afOverlay: null,
        afDrift: null,
        guidePulseScatter: null,
        guidePulseDuration: null,
        guidingSnr: null,
        autocenter: null
    };

    // --- AF Run Colors (per run number) ---
    const AF_RUN_COLORS = [
        '#3b82f6',  // Run 1: Blue
        '#22c55e',  // Run 2: Green
        '#ec4899',  // Run 3: Pink
        '#f97316',  // Run 4: Orange
        '#a855f7',  // Run 5: Purple
        '#06b6d4',  // Run 6: Cyan
        '#eab308',  // Run 7: Yellow
        '#ef4444',  // Run 8: Red
    ];
    const AF_DRIFT_COLOR = '#ffc107';  // Brand warning amber for drift line
    const AF_TEMP_DRIFT_COLOR = '#ffc107';  // Brand warning amber for temp drift card

    // --- Parabola Fitting Helpers (for V-curves) ---

    /**
     * Solve 3x3 linear system using Gaussian elimination with partial pivoting
     * @param {number[][]} A - 3x3 coefficient matrix
     * @param {number[]} b - Right-hand side vector
     * @returns {number[]|null} Solution vector or null if singular
     */
    function solveLinear3x3(A, b) {
        const M = A.map((row, i) => [...row, b[i]]);
        for (let col = 0; col < 3; col++) {
            let maxRow = col;
            for (let row = col + 1; row < 3; row++) {
                if (Math.abs(M[row][col]) > Math.abs(M[maxRow][col])) maxRow = row;
            }
            [M[col], M[maxRow]] = [M[maxRow], M[col]];
            if (Math.abs(M[col][col]) < 1e-10) return null;
            for (let row = col + 1; row < 3; row++) {
                const f = M[row][col] / M[col][col];
                for (let j = col; j <= 3; j++) M[row][j] -= f * M[col][j];
            }
        }
        const x = [0, 0, 0];
        for (let i = 2; i >= 0; i--) {
            x[i] = M[i][3] / M[i][i];
            for (let k = i - 1; k >= 0; k--) M[k][3] -= M[k][i] * x[i];
        }
        return x;
    }

    /**
     * Fit parabola to points using least-squares quadratic regression
     * @param {Array<{x: number, y: number}>} points - Data points
     * @returns {Object|null} {fn, vertex, a, b, c} or null if fit fails
     */
    function fitParabola(points) {
        const n = points.length;
        if (n < 3) return null;

        let s0 = n, s1 = 0, s2 = 0, s3 = 0, s4 = 0, t0 = 0, t1 = 0, t2 = 0;
        for (const p of points) {
            const x = p.x, y = p.y;
            s1 += x; s2 += x * x; s3 += x * x * x; s4 += x * x * x * x;
            t0 += y; t1 += x * y; t2 += x * x * y;
        }

        const A = [[s0, s1, s2], [s1, s2, s3], [s2, s3, s4]];
        const B = [t0, t1, t2];
        const coeffs = solveLinear3x3(A, B);
        if (!coeffs) return null;

        const [c, b, a] = coeffs;

        // Check for valid parabola (a > 0 for convex/V-shape)
        if (a <= 0) return null;

        const vertex = -b / (2 * a);

        return { fn: x => a * x * x + b * x + c, vertex, a, b, c };
    }

    /**
     * Generate evenly spaced values between min and max
     */
    function linspace(min, max, n) {
        const result = [];
        const step = (max - min) / (n - 1);
        for (let i = 0; i < n; i++) {
            result.push(min + i * step);
        }
        return result;
    }

    // --- Cached data ---
    let logData = null;

    /**
     * Initialize log analysis for a session
     * @param {number} sessionId - JournalSession ID
     */
    window.initSessionLogAnalysis = async function(sessionId) {
        if (!sessionId) return;

        // Show loading state
        const container = document.querySelector('.log-analysis-container');
        if (container) {
            container.classList.add('loading');
        }

        try {
            const response = await fetch(`/api/session/${sessionId}/log-analysis`);
            if (!response.ok) throw new Error('Failed to fetch log data');

            logData = await response.json();

            if (!logData.has_logs) {
                showNoLogsMessage();
                return;
            }

            // Initialize all charts
            renderOverviewTab();
            renderGuidingTab();
            renderDitheringTab();
            renderAutoFocusTab();
            renderNinaTab();

        } catch (err) {
            console.error('Error loading log analysis:', err);
            showErrorMessage(err.message);
        } finally {
            if (container) {
                container.classList.remove('loading');
            }
        }
    };

    /**
     * Show message when no logs are available
     */
    function showNoLogsMessage() {
        const container = document.querySelector('.log-analysis-container');
        if (container) {
            container.innerHTML = `
                <div style="text-align: center; padding: 40px; color: var(--text-muted);">
                    <p>No log files uploaded for this session.</p>
                    <p style="font-size: 0.9em;">Edit the session to upload ASIAIR, PHD2, or NINA logs.</p>
                </div>
            `;
        }
    }

    /**
     * Show error message
     */
    function showErrorMessage(message) {
        const container = document.querySelector('.log-analysis-container');
        if (container) {
            container.innerHTML = `
                <div style="text-align: center; padding: 40px; color: var(--danger-color);">
                    <p>Error loading log analysis: ${message}</p>
                </div>
            `;
        }
    }

    /**
     * Check if dark theme is active
     * Falls back to checking data-theme attribute directly if stylingUtils not available
     */
    function isDarkTheme() {
        if (window.stylingUtils && window.stylingUtils.isDarkTheme) {
            return window.stylingUtils.isDarkTheme();
        }
        // Fallback: check data-theme attribute directly
        return document.documentElement.getAttribute('data-theme') === 'dark';
    }

    /**
     * Get common Chart.js options
     * @param {string} title - Chart title
     * @param {string} yLabel - Y-axis label
     * @param {string} xLabel - X-axis label (default: 'Hours')
     * @returns {Object} Chart.js options object
     */
    function getChartOptions(title, yLabel, xLabel = 'Hours') {
        const themeColors = getThemeColors();
        return {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: {
                title: {
                    display: !!title,
                    text: title,
                    color: themeColors.text
                },
                legend: {
                    labels: {
                        color: themeColors.text,
                        usePointStyle: true,
                        pointStyle: 'circle'
                    }
                },
                tooltip: {
                    backgroundColor: themeColors.tooltipBg,
                    titleColor: themeColors.tooltipTitle,
                    bodyColor: themeColors.tooltipBody,
                    borderColor: themeColors.tooltipBorder,
                    borderWidth: 1
                }
            },
            scales: {
                x: {
                    title: {
                        display: true,
                        text: xLabel,
                        color: themeColors.text
                    },
                    ticks: {
                        color: themeColors.textMuted,
                        maxTicksLimit: 10
                    },
                    grid: {
                        color: themeColors.grid
                    }
                },
                y: {
                    title: {
                        display: true,
                        text: yLabel,
                        color: themeColors.text
                    },
                    ticks: {
                        color: themeColors.textMuted
                    },
                    grid: {
                        color: themeColors.grid
                    }
                }
            }
        };
    }

    /**
     * Update chart options for theme changes (mutates chart.options in-place)
     * This is called when theme changes to update colors without destroying charts.
     * @param {Chart} chart - Chart.js instance to update
     */
    function updateChartThemeColors(chart) {
        if (!chart || !chart.options) return;

        const themeColors = getThemeColors();

        // Update plugins
        if (chart.options.plugins) {
            if (chart.options.plugins.title) {
                chart.options.plugins.title.color = themeColors.text;
            }
            if (chart.options.plugins.legend && chart.options.plugins.legend.labels) {
                chart.options.plugins.legend.labels.color = themeColors.text;
            }
            if (chart.options.plugins.tooltip) {
                chart.options.plugins.tooltip.backgroundColor = themeColors.tooltipBg;
                chart.options.plugins.tooltip.titleColor = themeColors.tooltipTitle;
                chart.options.plugins.tooltip.bodyColor = themeColors.tooltipBody;
                chart.options.plugins.tooltip.borderColor = themeColors.tooltipBorder;
            }
        }

        // Update scales
        ['x', 'y'].forEach(function(axis) {
            if (chart.options.scales && chart.options.scales[axis]) {
                const scale = chart.options.scales[axis];
                if (scale.title) {
                    scale.title.color = themeColors.text;
                }
                if (scale.ticks) {
                    scale.ticks.color = themeColors.textMuted;
                }
                if (scale.grid) {
                    scale.grid.color = themeColors.grid;
                }
            }
        });
    }

    /**
     * Render Overview Tab - Key stats and swimlane timeline
     */
    function renderOverviewTab() {
        const asiair = logData.asiair;
        const phd2 = logData.phd2;
        const nina = logData.nina;

        // Update stat cards
        if (asiair) {
            document.getElementById('log-total-exposures').textContent =
                asiair.stats?.total_exposures || '-';
            document.getElementById('log-total-time').textContent =
                asiair.stats?.total_time_min ? `${Math.round(asiair.stats.total_time_min)} min` : '-';
            document.getElementById('log-dither-count').textContent =
                asiair.stats?.dither_count || '-';
            document.getElementById('log-af-count').textContent =
                asiair.stats?.af_count || '-';
        }

        if (phd2) {
            // Get total RMS - prefer stats, fall back to calculating from rms array
            let totalRmsAs = phd2.stats?.total_rms_as;
            if (!totalRmsAs && phd2.rms && phd2.rms.length > 0) {
                const raVals = phd2.rms.map(r => r[1]);
                const decVals = phd2.rms.map(r => r[2]);
                const raRms = Math.sqrt(raVals.reduce((sum, v) => sum + v * v, 0) / raVals.length);
                const decRms = Math.sqrt(decVals.reduce((sum, v) => sum + v * v, 0) / decVals.length);
                totalRmsAs = Math.sqrt(raRms * raRms + decRms * decRms).toFixed(3);
            }
            document.getElementById('log-guiding-rms').textContent =
                totalRmsAs ? `${totalRmsAs}"` : '-';
            document.getElementById('log-pixel-scale').textContent =
                phd2.pixel_scale ? `${phd2.pixel_scale}"/px` : '-';
            document.getElementById('log-frame-count').textContent =
                phd2.stats?.total_frames || '-';
        }

        // NINA stats (if no ASIAIR data)
        if (!asiair && nina) {
            // AF count from NINA autofocus_runs
            const afCount = nina.autofocus_runs?.length || 0;
            if (afCount > 0) {
                document.getElementById('log-af-count').textContent = afCount;
            }

            // Session duration
            if (nina.session_start && nina.session_end) {
                const start = new Date(nina.session_start);
                const end = new Date(nina.session_end);
                const duration = Math.round((end - start) / 60000);
                const hours = Math.floor(duration / 60);
                const mins = duration % 60;
                const durationStr = hours > 0 ? `${hours}h ${mins}m` : `${mins}m`;
                document.getElementById('log-total-time').textContent = durationStr;
            }
        }

        // Get session start time for clock display (prefer ASIAIR, fall back to PHD2, then NINA)
        const sessionStartStr = (asiair && asiair.session_start) ? asiair.session_start
                            : (phd2 && phd2.session_start) ? phd2.session_start
                            : (nina && nina.session_start) ? nina.session_start
                            : null;
        const sessionStart = sessionStartStr ? new Date(sessionStartStr) : null;

        // Helper to convert hours to clock time string
        const hoursToTime = (hours) => {
            if (!sessionStart) return hours.toFixed(1) + 'h';
            const date = new Date(sessionStart.getTime() + hours * 3600 * 1000);
            return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
        };

        // Render swimlane timeline
        // Use ASIAIR data if it has exposures, otherwise use NINA data
        const hasAsiairData = asiair?.exposures && asiair.exposures.length > 0;
        const hasNinaData = nina?.timeline_phases && nina?.timeline_phases.length > 0;

        console.log('[OVERVIEW_TAB] hasAsiairData:', hasAsiairData, 'hasNinaData:', hasNinaData);
        console.log('[OVERVIEW_TAB] nina.timeline_phases.length:', nina?.timeline_phases?.length);
        console.log('[OVERVIEW_TAB] nina.timeline_phases:', nina?.timeline_phases);

        if (hasAsiairData) {
            renderOverviewSwimlane(asiair, sessionStart, hoursToTime);
        } else if (hasNinaData) {
            renderNinaOverviewSwimlane(nina, sessionStart, hoursToTime);
        }

        // === Plate Solve Table ===
        renderPlateSolveTable(asiair, sessionStart, hoursToTime);

        // === Autocenter Chart ===
        renderAutocenterChart(asiair, sessionStart, hoursToTime);
    }

    /**
     * Render Overview Swimlane Timeline (SVG)
     */
    function renderOverviewSwimlane(asiair, sessionStart, hoursToTime) {
        const container = document.getElementById('log-overview-container');
        const svg = document.getElementById('log-overview-chart');
        const tooltip = document.getElementById('log-overview-tooltip');

        if (!svg || !container) return;

        // Collect all events
        const exposures = (asiair?.exposures || []).map(e => ({ h: e.h, type: 'exposure' }));
        const dithers = (asiair?.dithers || []).map(d => ({ h: d.h, type: 'dither', ok: d.ok }));
        const afRuns = (asiair?.af_runs || []).filter(r => r.h).map(r => ({ h: r.h, type: 'af' }));
        const meridianFlips = (asiair?.meridian_flips || []).filter(mf => mf.h).map(mf => ({ h: mf.h, type: 'mf' }));

        const allEvents = [...exposures, ...dithers, ...afRuns, ...meridianFlips];

        if (allEvents.length === 0) {
            container.innerHTML = '<p style="color: var(--text-muted); text-align: center; padding: 40px;">No timeline data to display.</p>';
            return;
        }

        // Calculate time range
        const maxHours = Math.max(...allEvents.map(e => e.h), 0.1);

        // Swimlane configuration
        const rowHeight = 36;
        const labelWidth = 90;
        const chartPadding = { left: 10, right: 20, top: 10, bottom: 25 };
        const rows = [
            { id: 'exposures', label: 'Exposures', color: COLORS.exposures, events: exposures },
            { id: 'dithers', label: 'Dithers', color: COLORS.dither, events: dithers },
            { id: 'af', label: 'AutoFocus', color: COLORS.af, events: afRuns },
            { id: 'mf', label: 'Meridian Flip', color: COLORS.meridianFlip, events: meridianFlips }
        ];

        // Calculate dimensions
        const totalHeight = rows.length * rowHeight + chartPadding.top + chartPadding.bottom;
        const containerWidth = container.clientWidth - 30; // Account for padding
        const chartWidth = containerWidth - labelWidth - chartPadding.left - chartPadding.right;

        // Set SVG dimensions
        svg.setAttribute('width', containerWidth);
        svg.setAttribute('height', totalHeight);
        svg.innerHTML = '';

        const dark = isDarkTheme();
        const themeColors = getThemeColors();
        const textColor = themeColors.textMuted;
        const labelColor = themeColors.text;
        const dividerColor = themeColors.grid;

        // Helper to convert hours to X position
        const hoursToX = (h) => labelWidth + chartPadding.left + (h / maxHours) * chartWidth;

        // Create SVG namespace helper
        const ns = 'http://www.w3.org/2000/svg';
        const createEl = (tag, attrs) => {
            const el = document.createElementNS(ns, tag);
            Object.entries(attrs || {}).forEach(([k, v]) => el.setAttribute(k, v));
            return el;
        };

        // Draw each row
        rows.forEach((row, rowIndex) => {
            const y = chartPadding.top + rowIndex * rowHeight;

            // Row label
            const label = createEl('text', {
                x: labelWidth - 10,
                y: y + rowHeight / 2 + 4,
                'text-anchor': 'end',
                'font-size': '12',
                'font-family': 'system-ui, -apple-system, sans-serif',
                fill: labelColor
            });
            label.textContent = row.label;
            svg.appendChild(label);

            // Row divider (except after last row)
            if (rowIndex < rows.length - 1) {
                const divider = createEl('line', {
                    x1: labelWidth,
                    y1: y + rowHeight,
                    x2: containerWidth - chartPadding.right,
                    y2: y + rowHeight,
                    stroke: dividerColor,
                    'stroke-width': 1
                });
                svg.appendChild(divider);
            }

            // Draw events
            row.events.forEach(event => {
                const x = hoursToX(event.h);
                const centerY = y + rowHeight / 2;

                if (row.id === 'exposures') {
                    // Thin vertical line for exposures
                    const line = createEl('line', {
                        x1: x,
                        y1: y + 4,
                        x2: x,
                        y2: y + rowHeight - 4,
                        stroke: row.color,
                        'stroke-width': 1,
                        'data-type': 'exposure',
                        'data-time': event.h
                    });
                    svg.appendChild(line);
                } else if (row.id === 'dithers') {
                    // Slightly thicker line for dithers
                    const color = event.ok ? row.color : '#c4889e';  // Soft rose for failed dithers
                    const line = createEl('line', {
                        x1: x,
                        y1: y + 4,
                        x2: x,
                        y2: y + rowHeight - 4,
                        stroke: color,
                        'stroke-width': 2,
                        'data-type': 'dither',
                        'data-time': event.h,
                        'data-ok': event.ok
                    });
                    svg.appendChild(line);
                } else if (row.id === 'af') {
                    // Star/asterisk for AutoFocus
                    const star = createEl('text', {
                        x: x,
                        y: centerY + 4,
                        'text-anchor': 'middle',
                        'font-size': '16',
                        'font-weight': 'bold',
                        fill: row.color,
                        'data-type': 'af',
                        'data-time': event.h
                    });
                    star.textContent = '✦';
                    svg.appendChild(star);
                } else if (row.id === 'mf') {
                    // Double vertical line for Meridian Flip
                    const line1 = createEl('line', {
                        x1: x - 3,
                        y1: y + 6,
                        x2: x - 3,
                        y2: y + rowHeight - 6,
                        stroke: row.color,
                        'stroke-width': 2
                    });
                    const line2 = createEl('line', {
                        x1: x + 3,
                        y1: y + 6,
                        x2: x + 3,
                        y2: y + rowHeight - 6,
                        stroke: row.color,
                        'stroke-width': 2
                    });
                    const group = createEl('g', {
                        'data-type': 'mf',
                        'data-time': event.h
                    });
                    group.appendChild(line1);
                    group.appendChild(line2);
                    svg.appendChild(group);
                }
            });
        });

        // Draw X axis labels
        const xAxisY = chartPadding.top + rows.length * rowHeight + 5;
        const numTicks = Math.min(10, Math.ceil(maxHours * 2) + 1);
        const tickInterval = maxHours / (numTicks - 1);

        for (let i = 0; i < numTicks; i++) {
            const h = i * tickInterval;
            const x = hoursToX(h);

            // Tick mark
            const tick = createEl('line', {
                x1: x,
                y1: xAxisY - 5,
                x2: x,
                y2: xAxisY,
                stroke: textColor,
                'stroke-width': 1
            });
            svg.appendChild(tick);

            // Label
            const label = createEl('text', {
                x: x,
                y: xAxisY + 12,
                'text-anchor': 'middle',
                'font-size': '11',
                'font-family': 'system-ui, -apple-system, sans-serif',
                fill: textColor
            });
            label.textContent = hoursToTime(h);
            svg.appendChild(label);
        }

        // Tooltip handling
        const showTooltip = (e, text) => {
            const rect = container.getBoundingClientRect();
            const x = e.clientX - rect.left + 10;
            const y = e.clientY - rect.top - 10;
            tooltip.textContent = text;
            tooltip.style.left = x + 'px';
            tooltip.style.top = y + 'px';
            tooltip.style.display = 'block';
        };

        const hideTooltip = () => {
            tooltip.style.display = 'none';
        };

        // Add event listeners for tooltips
        svg.querySelectorAll('[data-type]').forEach(el => {
            const type = el.getAttribute('data-type');
            const time = parseFloat(el.getAttribute('data-time'));
            const timeStr = hoursToTime(time);

            let text;
            if (type === 'exposure') {
                text = `Exposure at ${timeStr}`;
            } else if (type === 'dither') {
                const ok = el.getAttribute('data-ok') === 'true';
                text = `Dither at ${timeStr} (${ok ? 'settled' : 'timeout'})`;
            } else if (type === 'af') {
                text = `AutoFocus at ${timeStr}`;
            } else if (type === 'mf') {
                text = `Meridian Flip at ${timeStr}`;
            }

            el.style.cursor = 'pointer';
            el.addEventListener('mouseenter', (e) => showTooltip(e, text));
            el.addEventListener('mousemove', (e) => showTooltip(e, text));
            el.addEventListener('mouseleave', hideTooltip);
        });
    }

    /**
     * Render NINA Overview Swimlane Timeline (SVG)
     */
    function renderNinaOverviewSwimlane(nina, sessionStart, hoursToTime) {
        const container = document.getElementById('log-overview-container');
        const svg = document.getElementById('log-overview-chart');
        const tooltip = document.getElementById('log-overview-tooltip');

        if (!svg || !container) return;

        // Guard against null/undefined nina data
        if (!nina) {
            container.innerHTML = '<p style="color: var(--text-muted); text-align: center; padding: 40px;">No NINA timeline data to display.</p>';
            return;
        }

        // DEBUG: Log nina data structure
        console.log('[NINA_SWIMLANE] nina keys:', nina ? Object.keys(nina) : 'nina is null/undefined');
        console.log('[NINA_SWIMLANE] timeline_phases:', nina?.timeline_phases);
        console.log('[NINA_SWIMLANE] timeline_phases length:', nina?.timeline_phases ? nina.timeline_phases.length : 'undefined');

        const phases = nina.timeline_phases || [];
        if (phases.length === 0) {
            container.innerHTML = '<p style="color: var(--text-muted); text-align: center; padding: 40px;">No timeline data to display.</p>';
            return;
        }

        // Phase colors matching the spec
        const phaseColors = {
            imaging: '#83b4c5',      // teal
            focus: '#c09030',        // amber
            guiding: '#2a9060',      // green
            platesolve: '#7060c0',   // purple
            flats: '#608060',        // sage
            sequence: '#888888'      // grey
        };

        // Get unique badge classes for swimlane rows
        // FIX: Use badge_class if present, otherwise fall back to phase property
        const badgeClasses = [...new Set(phases.map(p => p.badge_class || p.phase).filter(Boolean))].sort();

        // Calculate session duration in hours from session start/end
        let sessionDurationHours = 0;
        let sessionStartTime = sessionStart;
        let sessionEndTime = sessionStart;

        // DEBUG: Log input values
        console.log('[SWIMLANE_TIME] Input sessionStart:', sessionStart, '(type:', typeof sessionStart, ')');
        console.log('[SWIMLANE_TIME] nina.session_start:', nina?.session_start, '(type:', typeof nina?.session_start, ')');
        console.log('[SWIMLANE_TIME] nina.session_end:', nina?.session_end, '(type:', typeof nina?.session_end, ')');

        if (nina?.session_start && nina?.session_end) {
            const start = new Date(nina.session_start);
            const end = new Date(nina.session_end);
            sessionStartTime = start;
            sessionEndTime = end;
            sessionDurationHours = (end - start) / (1000 * 60 * 60);
            console.log('[SWIMLANE_TIME] Using nina session times');
        } else if (phases.length > 0) {
            // Fallback to phase times
            const phaseStarts = phases.filter(p => p.start_time).map(p => new Date(p.start_time));
            // FIX: Only use phases that have end_time (filter out None/null values)
            const phaseEnds = phases.filter(p => p.end_time && p.end_time !== 'null').map(p => new Date(p.end_time));
            if (phaseStarts.length > 0) {
                sessionStartTime = new Date(Math.min(...phaseStarts));
            }
            if (phaseEnds.length > 0) {
                sessionEndTime = new Date(Math.max(...phaseEnds));
            }
            // FIX: If no phases have end_time (all point events), use the last start_time as end_time
            if (sessionStartTime && sessionEndTime === sessionStart) {
                const lastPhaseStart = Math.max(...phaseStarts);
                sessionEndTime = new Date(lastPhaseStart);
                sessionDurationHours = (sessionEndTime - sessionStartTime) / (1000 * 60 * 60);
            } else if (sessionStartTime && sessionEndTime) {
                sessionDurationHours = (sessionEndTime - sessionStartTime) / (1000 * 60 * 60);
            }
            console.log('[SWIMLANE_TIME] Using phase times (fallback)');
        }

        // DEBUG: Log calculated values
        console.log('[SWIMLANE_TIME] sessionStartTime:', sessionStartTime, '(type:', typeof sessionStartTime, ')');
        console.log('[SWIMLANE_TIME] sessionEndTime:', sessionEndTime, '(type:', typeof sessionEndTime, ')');
        console.log('[SWIMLANE_TIME] sessionDurationHours:', sessionDurationHours);

        // Ensure minimum duration
        sessionDurationHours = Math.max(sessionDurationHours, 0.1);

        // Swimlane configuration
        const rowHeight = 36;
        const labelWidth = 90;
        const chartPadding = { left: 10, right: 20, top: 10, bottom: 25 }; // Reduced bottom padding (legend removed)
        const statsHeight = 30; // Height for stats row above swimlane

        // DEBUG: Dump first phase of each badge_class to diagnose data structure
        const byClass = {};
        phases.forEach(p => {
            if (!byClass[p.badge_class]) byClass[p.badge_class] = p;
        });

        // DEBUG: Log the first phase structure to see what properties exist
        if (phases.length > 0) {
            console.log('[SWIMLANE_ROWS] First phase properties:', Object.keys(phases[0]));
        }


        // Check if badge_class property exists on phases
        const phasesWithoutBadge = phases.filter(p => !p.badge_class);
        if (phasesWithoutBadge.length > 0) {
        }

        // Prepare rows: one per badge_class
        const rows = badgeClasses.map(badgeClass => {
            // FIX: Filter by badge_class or phase property for flexibility
            const rowPhases = phases.filter(p => (p.badge_class || p.phase) === badgeClass);
            console.log(`[SWIMLANE_ROWS] ${badgeClass}: found ${rowPhases.length} phases`);
            return {
                id: badgeClass,
                label: badgeClass.charAt(0).toUpperCase() + badgeClass.slice(1),
                color: phaseColors[badgeClass] || '#888888',
                phases: rowPhases
            };
        });

        // Calculate dimensions
        const totalHeight = statsHeight + rows.length * rowHeight + chartPadding.top + chartPadding.bottom;
        const containerWidth = container.clientWidth - 30;
        const chartWidth = containerWidth - labelWidth - chartPadding.left - chartPadding.right;

        // DEBUG: Log canvas dimensions

        // Set SVG dimensions
        svg.setAttribute('width', containerWidth);
        svg.setAttribute('height', totalHeight);
        svg.innerHTML = '';

        const dark = isDarkTheme();
        const themeColors = getThemeColors();
        const textColor = themeColors.textMuted;
        const labelColor = themeColors.text;
        const dividerColor = themeColors.grid;

        // Create SVG namespace helper
        const ns = 'http://www.w3.org/2000/svg';
        const createEl = (tag, attrs) => {
            const el = document.createElementNS(ns, tag);
            Object.entries(attrs || {}).forEach(([k, v]) => el.setAttribute(k, v));
            return el;
        };

        // Helper to convert phase time to X position
        const phaseTimeToX = (timeStr) => {
            const time = new Date(timeStr);
            const hoursFromStart = (time - sessionStartTime) / (1000 * 60 * 60);
            const x = labelWidth + chartPadding.left + (hoursFromStart / sessionDurationHours) * chartWidth;
            // DEBUG: Log first few conversions per badge class (using a counter)
            if (!phaseTimeToX.callCount) phaseTimeToX.callCount = 0;
            if (phaseTimeToX.callCount < 5) {
                console.log(`[phaseTimeToX] timeStr:${timeStr} -> time:${time} -> hoursFromStart:${hoursFromStart} -> x:${x}`);
                phaseTimeToX.callCount++;
            }
            return x;
        };

        // Draw stats row at top
        const statsY = chartPadding.top;
        const afCount = nina.autofocus_runs?.length || 0;
        const statsText = `Session: ${hoursToTime(0)} - ${hoursToTime(sessionDurationHours)} | AF Runs: ${afCount}`;
        const statsLabel = createEl('text', {
            x: labelWidth + chartPadding.left,
            y: statsY + 18,
            'text-anchor': 'start',
            'font-size': '11',
            'font-family': 'system-ui, -apple-system, sans-serif',
            fill: textColor
        });
        statsLabel.textContent = statsText;
        svg.appendChild(statsLabel);

        // Draw each row
        rows.forEach((row, rowIndex) => {
            const y = statsY + statsHeight + rowIndex * rowHeight;

            // Row label
            const label = createEl('text', {
                x: labelWidth - 10,
                y: y + rowHeight / 2 + 4,
                'text-anchor': 'end',
                'font-size': '12',
                'font-family': 'system-ui, -apple-system, sans-serif',
                fill: labelColor
            });
            label.textContent = row.label;
            svg.appendChild(label);

            // Row divider (except after last row)
            if (rowIndex < rows.length - 1) {
                const divider = createEl('line', {
                    x1: labelWidth,
                    y1: y + rowHeight,
                    x2: containerWidth - chartPadding.right,
                    y2: y + rowHeight,
                    stroke: dividerColor,
                    'stroke-width': 1
                });
                svg.appendChild(divider);
            }

            // Draw phase bars
            row.phases.forEach((phase, idx) => {
                if (!phase.start_time) return;

                const x1 = phaseTimeToX(phase.start_time);
                let barWidth = 1; // Default to 1px for point events (focus)

                // If phase has end_time, calculate bar width
                if (phase.end_time) {
                    const x2 = phaseTimeToX(phase.end_time);
                    barWidth = Math.max(x2 - x1, 1);
                }

                // DEBUG: Log bar dimensions for first few phases per row
                if (idx < 2) {
                    console.log(`[SWIMLANE_BAR] ${row.id}[${idx}] start:${phase.start_time} end:${phase.end_time} x1:${x1} width:${barWidth}`);
                }

                // Phase bar (or point for focus/AF runs)
                const bar = createEl('rect', {
                    x: x1,
                    y: y + 8,
                    width: barWidth,
                    height: rowHeight - 16,
                    fill: row.color,
                    'fill-opacity': 0.85,
                    rx: 2,
                    ry: 2,
                    'data-type': 'phase',
                    'data-badge-class': phase.badge_class || phase.phase,
                    'data-start': phase.start_time,
                    'data-end': phase.end_time
                });
                svg.appendChild(bar);

                // Red dot above bar for errors
                if (phase.error_count > 0) {
                    const errorDot = createEl('circle', {
                        cx: x1 + barWidth / 2,
                        cy: y + 2,
                        r: 3,
                        fill: '#c05050',
                        'data-error-count': phase.error_count
                    });
                    svg.appendChild(errorDot);
                }
            });
        });

        // Draw X axis labels
        const xAxisY = statsY + statsHeight + rows.length * rowHeight + 5;
        const numTicks = Math.min(10, Math.ceil(sessionDurationHours * 2) + 1);
        const tickInterval = sessionDurationHours / (numTicks - 1);

        for (let i = 0; i < numTicks; i++) {
            const h = i * tickInterval;
            const x = labelWidth + chartPadding.left + (h / sessionDurationHours) * chartWidth;

            // Tick mark
            const tick = createEl('line', {
                x1: x,
                y1: xAxisY - 5,
                x2: x,
                y2: xAxisY,
                stroke: textColor,
                'stroke-width': 1
            });
            svg.appendChild(tick);

            // Label
            const label = createEl('text', {
                x: x,
                y: xAxisY + 12,
                'text-anchor': 'middle',
                'font-size': '11',
                'font-family': 'system-ui, -apple-system, sans-serif',
                fill: textColor
            });
            label.textContent = hoursToTime(h);
            svg.appendChild(label);
        }

        // Tooltip handling
        const showTooltip = (e, text) => {
            const rect = container.getBoundingClientRect();
            const x = e.clientX - rect.left + 10;
            const y = e.clientY - rect.top - 10;
            tooltip.textContent = text;
            tooltip.style.left = x + 'px';
            tooltip.style.top = y + 'px';
            tooltip.style.display = 'block';
        };

        const hideTooltip = () => {
            tooltip.style.display = 'none';
        };

        // Add event listeners for tooltips on phase bars
        svg.querySelectorAll('[data-type="phase"]').forEach(el => {
            const badgeClass = el.getAttribute('data-badge-class');
            const start = new Date(el.getAttribute('data-start'));
            const endStr = el.getAttribute('data-end');
            const duration = endStr ? Math.round((new Date(endStr) - start) / 60000) : 0;
            const startTimeStr = start.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });

            const errorDot = el.nextElementSibling;
            const errorCount = errorDot && errorDot.getAttribute('data-error-count')
                ? parseInt(errorDot.getAttribute('data-error-count'))
                : 0;

            let text = `${badgeClass} at ${startTimeStr}${duration > 0 ? ` (${duration}m)` : ' (point event)'}`;
            if (errorCount > 0) {
                text += ` • ${errorCount} error${errorCount > 1 ? 's' : ''}`;
            }

            el.style.cursor = 'pointer';
            el.addEventListener('mouseenter', (e) => showTooltip(e, text));
            el.addEventListener('mousemove', (e) => showTooltip(e, text));
            el.addEventListener('mouseleave', hideTooltip);
        });
    }

    /**
     * Parse RA string (e.g., "12h34m56s") to decimal degrees
     */
    function parseRaToDegrees(raStr) {
        if (!raStr) return null;
        try {
            const match = raStr.match(/(\d+)h(\d+)m(\d+(?:\.\d+)?)s/i);
            if (match) {
                const hours = parseFloat(match[1]);
                const mins = parseFloat(match[2]);
                const secs = parseFloat(match[3]);
                return (hours + mins / 60 + secs / 3600) * 15;  // 1 hour = 15 degrees
            }
            console.warn('Failed to parse RA string:', raStr);
            return null;
        } catch (e) {
            console.warn('Error parsing RA string:', raStr, e);
            return null;
        }
    }

    /**
     * Parse Dec string (e.g., "+12°34'56\"") to decimal degrees
     */
    function parseDecToDegrees(decStr) {
        if (!decStr) return null;
        try {
            const match = decStr.match(/([+-]?)(\d+)[°](\d+)'(\d+(?:\.\d+)?)"?/);
            if (match) {
                const sign = match[1] === '-' ? -1 : 1;
                const deg = parseFloat(match[2]);
                const mins = parseFloat(match[3]);
                const secs = parseFloat(match[4]);
                return sign * (deg + mins / 60 + secs / 3600);
            }
            console.warn('Failed to parse Dec string:', decStr);
            return null;
        } catch (e) {
            console.warn('Error parsing Dec string:', decStr, e);
            return null;
        }
    }

    /**
     * Render Plate Solve Results Table
     */
    function renderPlateSolveTable(asiair, sessionStart, hoursToTime) {
        const panel = document.getElementById('log-plate-solve-panel');
        const tableBody = document.querySelector('#log-plate-solve-table tbody');
        const driftEl = document.getElementById('log-pointing-drift');

        if (!panel || !tableBody) return;

        const solves = asiair?.plate_solves || [];
        if (solves.length === 0) {
            panel.style.display = 'none';
            return;
        }

        panel.style.display = 'block';
        tableBody.innerHTML = '';

        solves.forEach((solve, idx) => {
            const row = document.createElement('tr');
            const timeStr = hoursToTime(solve.h);
            const angle = solve.angle !== null && solve.angle !== undefined
                ? `${solve.angle.toFixed(1)}°`
                : '-';
            const stars = solve.stars || 0;

            row.innerHTML = `
                <td>${idx + 1}</td>
                <td>${timeStr}</td>
                <td style="font-family: monospace; font-size: 0.9em;">${solve.ra || '-'}</td>
                <td style="font-family: monospace; font-size: 0.9em;">${solve.dec || '-'}</td>
                <td>${angle}</td>
                <td>${stars}</td>
                <td><span class="log-status-badge log-status-success">✓ Solved</span></td>
            `;
            tableBody.appendChild(row);
        });

        // Calculate pointing drift if 2+ solves
        if (solves.length >= 2) {
            const first = solves[0];
            const last = solves[solves.length - 1];

            const raFirst = parseRaToDegrees(first.ra);
            const raLast = parseRaToDegrees(last.ra);
            const decFirst = parseDecToDegrees(first.dec);
            const decLast = parseDecToDegrees(last.dec);
            const timeSpan = last.h - first.h;

            // Only show drift if time span > 5 minutes (0.083 hours)
            if (timeSpan > 0.083 && raFirst !== null && raLast !== null && decFirst !== null && decLast !== null) {
                const raDrift = raLast - raFirst;
                const decDrift = decLast - decFirst;
                const angleDrift = (last.angle || 0) - (first.angle || 0);

                driftEl.innerHTML = `
                    <span>Pointing drift over ${timeSpan.toFixed(1)}h —</span>
                    <span>RA: <strong>${raDrift >= 0 ? '+' : ''}${raDrift.toFixed(2)}°</strong></span>
                    <span>Dec: <strong>${decDrift >= 0 ? '+' : ''}${decDrift.toFixed(2)}°</strong></span>
                    <span>Rotation: <strong>${angleDrift >= 0 ? '+' : ''}${angleDrift.toFixed(1)}°</strong></span>
                `;
                driftEl.style.display = 'flex';
            } else {
                driftEl.style.display = 'none';
            }
        } else {
            driftEl.style.display = 'none';
        }
    }

    /**
     * Render Autocenter Accuracy Chart
     */
    function renderAutocenterChart(asiair, sessionStart, hoursToTime) {
        const panel = document.getElementById('log-autocenter-panel');
        const canvas = document.getElementById('log-autocenter-chart');
        const statsEl = document.getElementById('log-autocenter-stats');

        if (!panel || !canvas) return;

        const autocenters = asiair?.autocenters || [];

        // Need at least 2 entries for a meaningful chart
        if (autocenters.length < 2) {
            panel.style.display = 'none';
            return;
        }

        panel.style.display = 'block';

        // Calculate stats
        const total = autocenters.length;
        const centeredCount = autocenters.filter(ac => ac.centered).length;
        const successRate = total > 0 ? Math.round((centeredCount / total) * 100) : 0;
        const distances = autocenters.map(ac => ac.distance_pct || 0);
        const avgDistance = distances.reduce((a, b) => a + b, 0) / distances.length;
        const bestDistance = Math.min(...distances);
        const worstDistance = Math.max(...distances);

        statsEl.innerHTML = `
            <span>Total: <strong>${total}</strong></span>
            <span>Success: <strong>${successRate}%</strong></span>
            <span>Avg: <strong>${avgDistance.toFixed(2)}%</strong></span>
            <span>Best: <strong>${bestDistance.toFixed(2)}%</strong></span>
            <span>Worst: <strong>${worstDistance.toFixed(2)}%</strong></span>
        `;

        if (charts.autocenter) charts.autocenter.destroy();

        const dark = isDarkTheme();

        // Check if time spread is meaningful (> 5 minutes = 0.083 hours)
        const hours = autocenters.map(ac => ac.h);
        const timeSpread = Math.max(...hours) - Math.min(...hours);
        const useTimeAxis = timeSpread > 0.083;

        // Prepare labels based on time spread
        let labels, xTitle;
        if (useTimeAxis) {
            labels = autocenters.map(ac => hoursToTime(ac.h));
            xTitle = 'Time';
        } else {
            labels = autocenters.map((_, i) => `#${i + 1}`);
            xTitle = 'Attempt #';
        }

        charts.autocenter = new Chart(canvas, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Distance (%)',
                    data: autocenters.map(ac => ac.distance_pct || 0),
                    backgroundColor: autocenters.map(ac =>
                        ac.centered ? 'rgba(72, 199, 142, 0.7)' : 'rgba(255, 99, 132, 0.7)'
                    ),
                    borderColor: autocenters.map(ac =>
                        ac.centered ? 'rgba(72, 199, 142, 1)' : 'rgba(255, 99, 132, 1)'
                    ),
                    borderWidth: 1
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            title: function(context) {
                                const idx = context[0].dataIndex;
                                if (useTimeAxis) {
                                    return hoursToTime(autocenters[idx].h);
                                } else {
                                    return `Attempt ${idx + 1}`;
                                }
                            },
                            afterLabel: function(context) {
                                const ac = autocenters[context.dataIndex];
                                const deg = ac.distance_deg ? `${ac.distance_deg.toFixed(3)}°` : '-';
                                const centered = ac.centered ? 'Yes' : 'No';
                                return [`Distance: ${deg}`, `Centered: ${centered}`];
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        title: { display: true, text: xTitle, color: dark ? COLORS.text : '#333' },
                        ticks: {
                            color: dark ? COLORS.text : '#666',
                            maxTicksLimit: 10
                        },
                        grid: { color: dark ? COLORS.grid : 'rgba(0, 0, 0, 0.1)' }
                    },
                    y: {
                        min: 0,
                        title: { display: true, text: 'Distance (%)', color: dark ? COLORS.text : '#333' },
                        ticks: { color: dark ? COLORS.text : '#666' },
                        grid: { color: dark ? COLORS.grid : 'rgba(0, 0, 0, 0.1)' }
                    }
                }
            }
        });
    }

    /**
     * Render Guiding Tab - RA/Dec corrections and RMS over time
     */
    function renderGuidingTab() {
        const phd2 = logData.phd2;
        if (!phd2) {
            const tab = document.getElementById('log-guiding-tab');
            if (tab) {
                tab.innerHTML = '<p style="color: var(--text-muted); text-align: center; padding: 40px;">No PHD2 guide log data available.</p>';
            }
            return;
        }

        // Update stats - show imaging-only if available, else fall back to all-frames
        const hasImagingStats = phd2.stats?.imaging && phd2.settle_windows?.length > 0;
        const allStats = phd2.stats?.all || phd2.stats;
        const imagingStats = phd2.stats?.imaging || allStats;

        // Helper: calculate RMS from phd2.rms array when stats are missing
        // phd2.rms format: [h, ra_rms_as, dec_rms_as, total_rms_as]
        const calculateRmsFromRmsArray = () => {
            if (!phd2.rms || phd2.rms.length === 0) return null;
            const raVals = phd2.rms.map(r => r[1]);
            const decVals = phd2.rms.map(r => r[2]);
            const raRms = Math.sqrt(raVals.reduce((sum, v) => sum + v * v, 0) / raVals.length);
            const decRms = Math.sqrt(decVals.reduce((sum, v) => sum + v * v, 0) / decVals.length);
            const totalRms = Math.sqrt(raRms * raRms + decRms * decRms);
            return { ra_rms_as: raRms.toFixed(3), dec_rms_as: decRms.toFixed(3), total_rms_as: totalRms.toFixed(3) };
        };

        // Get stats - prefer parsed stats, fall back to calculating from rms array
        const finalStats = (imagingStats?.ra_rms_as !== undefined) ? imagingStats : (calculateRmsFromRmsArray() || {});

        // Primary row: imaging-only (or all-frames if no settle data)
        document.getElementById('log-ra-rms').textContent = finalStats.ra_rms_as || '-';
        document.getElementById('log-dec-rms').textContent = finalStats.dec_rms_as || '-';
        document.getElementById('log-total-rms').textContent = finalStats.total_rms_as || '-';
        document.getElementById('log-frame-count').textContent = finalStats.frame_count?.toLocaleString() || '-';

        // Secondary row: all-frames (only if we have imaging stats)
        const allFramesRow = document.getElementById('log-all-frames-stats');
        if (hasImagingStats && allFramesRow) {
            document.getElementById('log-all-ra-rms').textContent = allStats.ra_rms_as || '-';
            document.getElementById('log-all-dec-rms').textContent = allStats.dec_rms_as || '-';
            document.getElementById('log-all-total-rms').textContent = allStats.total_rms_as || '-';
            allFramesRow.style.display = '';
        } else if (allFramesRow) {
            // No settle windows - hide secondary row
            allFramesRow.style.display = 'none';
        }

        // Get session start time for clock display
        // Also check ASIAIR session_start as fallback in case PHD2 log doesn't have it
        const asiair = logData.asiair;
        const sessionStartStr = (phd2 && phd2.session_start) ? phd2.session_start
                            : (asiair && asiair.session_start) ? asiair.session_start
                            : null;
        const sessionStart = sessionStartStr ? new Date(sessionStartStr) : null;

        // Debug: log session start for troubleshooting
        if (sessionStart) {
        } else {
        }

        // Helper to convert hours to clock time string
        const hoursToTime = (hours) => {
            if (!sessionStart) return hours.toFixed(1) + 'h';
            const date = new Date(sessionStart.getTime() + hours * 3600 * 1000);
            return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
        };

        // Dark theme flag (used by all charts in this tab)
        const dark = isDarkTheme();

        // RMS Chart
        const rmsCanvas = document.getElementById('log-guiding-chart');
        if (rmsCanvas && phd2.rms && phd2.rms.length > 0) {
            if (charts.guiding) charts.guiding.destroy();
            // Use actual last data point for max (no padding beyond data)
            const maxHours = phd2.rms[phd2.rms.length - 1][0];

            // Calculate zoomed Y-max: total RMS (all frames) × 2
            const totalRmsAll = phd2.stats?.all?.total_rms_as || phd2.stats?.total_rms_as || 5;
            const zoomedYMax = totalRmsAll * 2;

            // Track zoom state
            let guidingZoomed = false;

            // --- Detect gaps in RMS data to break connecting lines ---
            // Calculate median time interval from first 50 intervals
            const rms = phd2.rms;
            const intervals = [];
            for (let i = 0; i < Math.min(rms.length - 1, 50); i++) {
                intervals.push(rms[i + 1][0] - rms[i][0]);
            }
            const typicalInterval = intervals.length > 0
                ? intervals.sort((a, b) => a - b)[Math.floor(intervals.length / 2)]
                : 0.1;  // Default 6 minutes if no intervals

            // Build datasets with null gaps where time difference > typicalInterval * 5
            const raData = [];
            const decData = [];
            const totalData = [];
            const gapThreshold = typicalInterval * 5;

            for (let i = 0; i < rms.length; i++) {
                raData.push({ x: rms[i][0], y: rms[i][1] });
                decData.push({ x: rms[i][0], y: rms[i][2] });
                totalData.push({ x: rms[i][0], y: rms[i][3] });

                // If gap to next point exceeds threshold, insert null to break the line
                if (i < rms.length - 1 && (rms[i + 1][0] - rms[i][0]) > gapThreshold) {
                    const nullX = rms[i][0] + 0.001;
                    raData.push({ x: nullX, y: null });
                    decData.push({ x: nullX, y: null });
                    totalData.push({ x: nullX, y: null });
                }
            }

            charts.guiding = new Chart(rmsCanvas, {
                type: 'line',
                data: {
                    datasets: [
                        {
                            label: 'RA RMS (")',
                            data: raData,
                            borderColor: COLORS.ra,
                            backgroundColor: 'rgba(96, 165, 250, 0.1)',
                            borderWidth: 2,
                            pointRadius: 0,
                            tension: 0.2,
                            fill: true,
                            spanGaps: false
                        },
                        {
                            label: 'Dec RMS (")',
                            data: decData,
                            borderColor: COLORS.dec,
                            backgroundColor: 'rgba(244, 114, 182, 0.1)',
                            borderWidth: 2,
                            pointRadius: 0,
                            tension: 0.2,
                            fill: true,
                            spanGaps: false
                        },
                        {
                            label: 'Total RMS (")',
                            data: totalData,
                            borderColor: COLORS.total,
                            backgroundColor: 'transparent',
                            borderWidth: 2,
                            borderDash: [5, 5],
                            pointRadius: 0,
                            tension: 0.2,
                            spanGaps: false
                        }
                    ]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: false,
                    plugins: {
                        title: {
                            display: true,
                            text: 'Guiding RMS Over Time',
                            color: dark ? COLORS.text : '#333'
                        },
                        legend: {
                            labels: { color: dark ? COLORS.text : '#333' }
                        },
                        tooltip: {
                            mode: 'index',
                            intersect: false,
                            callbacks: {
                                title: function(items) {
                                    if (items.length > 0) {
                                        return hoursToTime(items[0].parsed.x);
                                    }
                                    return '';
                                }
                            }
                        }
                    },
                    scales: {
                        x: {
                            type: 'linear',
                            min: 0,
                            max: maxHours,  // End exactly at last data point
                            title: { display: true, text: 'Time (Local)', color: dark ? COLORS.text : '#333' },
                            ticks: {
                                color: dark ? COLORS.text : '#666',
                                callback: function(value) {
                                    return hoursToTime(value);
                                }
                            },
                            grid: { color: dark ? COLORS.grid : 'rgba(0, 0, 0, 0.1)' }
                        },
                        y: {
                            min: 0,
                            max: 15,  // Default cap at 15" - zoomed mode uses total_rms × 2
                            title: { display: true, text: 'RMS (arcsec)', color: dark ? COLORS.text : '#333' },
                            ticks: { color: dark ? COLORS.text : '#666' },
                            grid: { color: dark ? COLORS.grid : 'rgba(0, 0, 0, 0.1)' }
                        }
                    }
                }
            });

            // Add zoom toggle button handler
            const zoomBtn = document.getElementById('log-guiding-zoom-btn');
            if (zoomBtn) {
                zoomBtn.onclick = function() {
                    guidingZoomed = !guidingZoomed;
                    if (guidingZoomed) {
                        charts.guiding.options.scales.y.max = zoomedYMax;
                        zoomBtn.textContent = '⛶ Full';
                    } else {
                        charts.guiding.options.scales.y.max = 15;
                        zoomBtn.textContent = '⛶ Zoom';
                    }
                    charts.guiding.update('none'); // Instant update without animation
                };
            }
        }

        // === GUIDE PULSE ANALYSIS ===
        if (phd2.frames && phd2.frames.length > 0) {
            const frames = phd2.frames;

            // Calculate stats and check direction data availability
            const raPulses = frames.map(f => f[4]).filter(v => v !== null && v !== 0);
            const decPulses = frames.map(f => f[5]).filter(v => v !== null && v !== 0);
            const raDurs = frames.map(f => f[8]).filter(v => v !== null && v !== 0);
            const decDurs = frames.map(f => f[9]).filter(v => v !== null && v !== 0);

            const raDirs = frames.map(f => f[6]).filter(v => v !== null && v !== '');
            const decDirs = frames.map(f => f[7]).filter(v => v !== null && v !== '');

            const directionNullRatio = 1 - ((raDirs.length + decDirs.length) / (frames.length * 2));
            const hasGoodDirectionData = directionNullRatio < 0.8;

            // Calculate averages
            const avgRaPulse = raPulses.length > 0 ? (raPulses.reduce((a, b) => a + b, 0) / raPulses.length) : 0;
            const avgDecPulse = decPulses.length > 0 ? (decPulses.reduce((a, b) => a + b, 0) / decPulses.length) : 0;
            const avgRaDur = raDurs.length > 0 ? (raDurs.reduce((a, b) => a + b, 0) / raDurs.length) : 0;
            const avgDecDur = decDurs.length > 0 ? (decDurs.reduce((a, b) => a + b, 0) / decDurs.length) : 0;

            // Calculate dominant directions
            const raEastCount = raDirs.filter(d => d === 'E').length;
            const raWestCount = raDirs.filter(d => d === 'W').length;
            const decNorthCount = decDirs.filter(d => d === 'N').length;
            const decSouthCount = decDirs.filter(d => d === 'S').length;

            const dominantRaDir = raEastCount > raWestCount ? 'E' : (raWestCount > raEastCount ? 'W' : 'tie');
            const dominantDecDir = decNorthCount > decSouthCount ? 'N' : (decSouthCount > decNorthCount ? 'S' : 'tie');

            // Update stats panel
            const pulseStatsEl = document.getElementById('log-pulse-stats');
            if (pulseStatsEl) {
                document.getElementById('log-avg-ra-pulse').textContent = avgRaPulse.toFixed(2);
                document.getElementById('log-avg-dec-pulse').textContent = avgDecPulse.toFixed(2);
                document.getElementById('log-avg-ra-dur').textContent = Math.round(avgRaDur);
                document.getElementById('log-avg-dec-dur').textContent = Math.round(avgDecDur);

                if (hasGoodDirectionData) {
                    document.getElementById('log-ra-dominant-container').style.display = 'inline';
                    document.getElementById('log-dec-dominant-container').style.display = 'inline';
                    document.getElementById('log-ra-dominant').textContent = dominantRaDir === 'tie' ? 'E=W' : dominantRaDir;
                    document.getElementById('log-dec-dominant').textContent = dominantDecDir === 'tie' ? 'N=S' : dominantDecDir;
                }

                pulseStatsEl.style.display = 'flex';
            }

            // === GUIDE PULSE SCATTER CHART ===
            const scatterCanvas = document.getElementById('log-guide-pulse-scatter-chart');
            if (scatterCanvas) {
                if (charts.guidePulseScatter) charts.guidePulseScatter.destroy();

                const dark = isDarkTheme();

                // Filter frames with non-zero guide distances (actual corrections)
                const correctionFrames = frames.filter(f =>
                    (f[4] !== null && f[4] !== 0) || (f[5] !== null && f[5] !== 0)
                );

                // Downsample if needed
                const step = Math.max(1, Math.floor(correctionFrames.length / 1000));
                const sampledCorrections = correctionFrames.filter((_, i) => i % step === 0);

                // Percentile helper for axis calculation (local scope)
                function percentile(arr, p) {
                    if (!arr.length) return 0;
                    const sorted = [...arr].sort((a, b) => a - b);
                    const idx = Math.floor(sorted.length * p);
                    return sorted[Math.min(idx, sorted.length - 1)];
                }

                // Calculate axis range using 95th percentile (avoid outlier distortion)
                const raValues = sampledCorrections.map(f => Math.abs(f[4] || 0));
                const decValues = sampledCorrections.map(f => Math.abs(f[5] || 0));
                const maxAbs = Math.max(0.1, percentile(raValues, 0.95), percentile(decValues, 0.95)) * 1.15;

                // Separate normal points from outliers (beyond axis range)
                const isOutlier = (f) => Math.abs(f[4] || 0) > maxAbs || Math.abs(f[5] || 0) > maxAbs;

                // Create separate datasets for direction-based legend + outliers
                let scatterDatasets;
                if (hasGoodDirectionData) {
                    const eastFrames = sampledCorrections.filter(f => f[6] === 'E' && !isOutlier(f));
                    const westFrames = sampledCorrections.filter(f => f[6] === 'W' && !isOutlier(f));
                    const otherFrames = sampledCorrections.filter(f => f[6] !== 'E' && f[6] !== 'W' && !isOutlier(f));
                    const outlierFrames = sampledCorrections.filter(isOutlier);

                    scatterDatasets = [
                        {
                            label: 'East (RA+)',
                            data: eastFrames.map(f => ({ x: f[4] || 0, y: f[5] || 0, frame: f })),
                            backgroundColor: 'rgba(99, 179, 237, 0.6)',
                            pointRadius: 3,
                            pointHoverRadius: 5
                        },
                        {
                            label: 'West (RA-)',
                            data: westFrames.map(f => ({ x: f[4] || 0, y: f[5] || 0, frame: f })),
                            backgroundColor: 'rgba(252, 129, 74, 0.6)',
                            pointRadius: 3,
                            pointHoverRadius: 5
                        },
                        {
                            label: 'Other/Unknown',
                            data: otherFrames.map(f => ({ x: f[4] || 0, y: f[5] || 0, frame: f })),
                            backgroundColor: 'rgba(160, 160, 160, 0.4)',
                            pointRadius: 3,
                            pointHoverRadius: 5
                        },
                        {
                            label: 'Outliers',
                            data: outlierFrames.map(f => ({ x: f[4] || 0, y: f[5] || 0, frame: f })),
                            backgroundColor: 'rgba(255, 99, 132, 0.7)',
                            pointRadius: 4,
                            pointHoverRadius: 6,
                            pointStyle: 'triangle'
                        }
                    ].filter(ds => ds.data.length > 0);
                } else {
                    const normalFrames = sampledCorrections.filter(f => !isOutlier(f));
                    const outlierFrames = sampledCorrections.filter(isOutlier);

                    scatterDatasets = [
                        {
                            label: 'Guide Corrections',
                            data: normalFrames.map(f => ({ x: f[4] || 0, y: f[5] || 0, frame: f })),
                            backgroundColor: 'rgba(96, 165, 250, 0.5)',
                            pointRadius: 3,
                            pointHoverRadius: 5
                        },
                        {
                            label: 'Outliers',
                            data: outlierFrames.map(f => ({ x: f[4] || 0, y: f[5] || 0, frame: f })),
                            backgroundColor: 'rgba(255, 99, 132, 0.7)',
                            pointRadius: 4,
                            pointHoverRadius: 6,
                            pointStyle: 'triangle'
                        }
                    ].filter(ds => ds.data.length > 0);
                }

                // Custom plugin to draw crosshair at origin
                const crosshairPlugin = {
                    id: 'originCrosshair',
                    beforeDraw: (chart) => {
                        const ctx = chart.ctx;
                        const xAxis = chart.scales.x;
                        const yAxis = chart.scales.y;

                        if (!xAxis || !yAxis) return;

                        ctx.save();
                        ctx.strokeStyle = dark ? 'rgba(150, 150, 150, 0.5)' : 'rgba(100, 100, 100, 0.5)';
                        ctx.lineWidth = 1;
                        ctx.setLineDash([4, 4]);

                        // Vertical line at x=0 using scale conversion
                        const xZero = xAxis.getPixelForValue(0);
                        if (xZero >= xAxis.left && xZero <= xAxis.right) {
                            ctx.beginPath();
                            ctx.moveTo(xZero, yAxis.top);
                            ctx.lineTo(xZero, yAxis.bottom);
                            ctx.stroke();
                        }

                        // Horizontal line at y=0 using scale conversion
                        const yZero = yAxis.getPixelForValue(0);
                        if (yZero >= yAxis.top && yZero <= yAxis.bottom) {
                            ctx.beginPath();
                            ctx.moveTo(xAxis.left, yZero);
                            ctx.lineTo(xAxis.right, yZero);
                            ctx.stroke();
                        }

                        ctx.restore();
                    }
                };

                charts.guidePulseScatter = new Chart(scatterCanvas, {
                    type: 'scatter',
                    plugins: [crosshairPlugin],
                    data: { datasets: scatterDatasets },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        animation: false,
                        plugins: {
                            title: {
                                display: true,
                                text: 'Guide Pulse Scatter',
                                color: dark ? COLORS.text : '#333'
                            },
                            subtitle: {
                                display: true,
                                text: 'Each point = one guide frame correction',
                                color: dark ? COLORS.text : 'rgba(100, 100, 100, 0.7)',
                                font: { size: 11 }
                            },
                            legend: {
                                display: hasGoodDirectionData,
                                labels: { color: dark ? COLORS.text : '#333' }
                            },
                            tooltip: {
                                callbacks: {
                                    label: function(context) {
                                        const frame = context.raw.frame;
                                        const raDir = frame[6] || '-';
                                        const decDir = frame[7] || '-';
                                        const snr = frame[3] !== null ? frame[3].toFixed(1) : '-';
                                        return `RA: ${context.parsed.x.toFixed(2)}px ${raDir} | Dec: ${context.parsed.y.toFixed(2)}px ${decDir} | SNR: ${snr}`;
                                    }
                                }
                            }
                        },
                        scales: {
                            x: {
                                min: -maxAbs,
                                max: maxAbs,
                                title: { display: true, text: 'RA Correction (px)', color: dark ? COLORS.text : '#333' },
                                ticks: { color: dark ? COLORS.text : '#666' },
                                grid: { color: dark ? COLORS.grid : 'rgba(0, 0, 0, 0.1)' }
                            },
                            y: {
                                min: -maxAbs,
                                max: maxAbs,
                                title: { display: true, text: 'Dec Correction (px)', color: dark ? COLORS.text : '#333' },
                                ticks: { color: dark ? COLORS.text : '#666' },
                                grid: { color: dark ? COLORS.grid : 'rgba(0, 0, 0, 0.1)' }
                            }
                        }
                    }
                });
            }

            // === GUIDE PULSE DURATION CHART ===
            const durationCanvas = document.getElementById('log-guide-pulse-duration-chart');
            if (durationCanvas) {
                if (charts.guidePulseDuration) charts.guidePulseDuration.destroy();

                const dark = isDarkTheme();

                // Filter frames with non-zero durations
                const raDurData = frames
                    .filter(f => f[8] !== null && f[8] !== 0)
                    .map(f => ({ x: f[0], y: f[8] }));
                const decDurData = frames
                    .filter(f => f[9] !== null && f[9] !== 0)
                    .map(f => ({ x: f[0], y: f[9] }));

                // Get max hours from RMS data or frames
                const durMaxHours = phd2.rms && phd2.rms.length > 0
                    ? phd2.rms[phd2.rms.length - 1][0]
                    : (frames.length > 0 ? frames[frames.length - 1][0] : undefined);

                charts.guidePulseDuration = new Chart(durationCanvas, {
                    type: 'line',
                    data: {
                        datasets: [
                            {
                                label: 'RA Duration (ms)',
                                data: raDurData,
                                borderColor: COLORS.ra,
                                backgroundColor: 'transparent',
                                borderWidth: 1,
                                pointRadius: 0,
                                tension: 0
                            },
                            {
                                label: 'Dec Duration (ms)',
                                data: decDurData,
                                borderColor: COLORS.dec,
                                backgroundColor: 'transparent',
                                borderWidth: 1,
                                pointRadius: 0,
                                tension: 0
                            }
                        ]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        animation: false,
                        plugins: {
                            title: {
                                display: true,
                                text: 'Guide Pulse Duration Over Time',
                                color: dark ? COLORS.text : '#333'
                            },
                            legend: {
                                labels: { color: dark ? COLORS.text : '#333' }
                            },
                            tooltip: {
                                mode: 'index',
                                intersect: false,
                                callbacks: {
                                    title: function(items) {
                                        if (items.length > 0) {
                                            return hoursToTime(items[0].parsed.x);
                                        }
                                        return '';
                                    }
                                }
                            }
                        },
                        scales: {
                            x: {
                                type: 'linear',
                                min: 0,
                                max: durMaxHours,
                                title: { display: true, text: 'Time (Local)', color: dark ? COLORS.text : '#333' },
                                ticks: {
                                    color: dark ? COLORS.text : '#666',
                                    callback: function(value) {
                                        return hoursToTime(value);
                                    }
                                },
                                grid: { color: dark ? COLORS.grid : 'rgba(0, 0, 0, 0.1)' }
                            },
                            y: {
                                min: 0,
                                title: { display: true, text: 'Pulse Duration (ms)', color: dark ? COLORS.text : '#333' },
                                ticks: { color: dark ? COLORS.text : '#666' },
                                grid: { color: dark ? COLORS.grid : 'rgba(0, 0, 0, 0.1)' }
                            }
                        }
                    }
                });
            }
        }

        // === GUIDE STAR SNR CHART ===
        renderGuidingSnrChart(phd2, sessionStart, hoursToTime, dark);
    }

    /**
     * Render Guide Star SNR Chart
     */
    function renderGuidingSnrChart(phd2, sessionStart, hoursToTime, dark) {
        const statsContainer = document.getElementById('log-snr-stats');
        const chartContainer = document.getElementById('log-snr-chart-container');
        const narrativeContainer = document.getElementById('log-snr-narrative');
        const canvas = document.getElementById('logGuidingSnrChart');

        if (!phd2 || !phd2.frames || phd2.frames.length === 0) {
            if (statsContainer) statsContainer.style.display = 'none';
            if (chartContainer) chartContainer.style.display = 'none';
            if (narrativeContainer) narrativeContainer.style.display = 'none';
            return;
        }

        // Extract SNR data (index 3), filtering out null/0 values
        const frames = phd2.frames;
        const snrData = [];
        const validSnrs = [];

        for (let i = 0; i < frames.length; i++) {
            const h = frames[i][0];
            const snr = frames[i][3];
            if (snr !== null && snr !== undefined && snr > 0) {
                snrData.push({ h, snr });
                validSnrs.push(snr);
            }
        }

        if (validSnrs.length === 0) {
            if (statsContainer) statsContainer.style.display = 'none';
            if (chartContainer) chartContainer.style.display = 'none';
            if (narrativeContainer) narrativeContainer.style.display = 'none';
            return;
        }

        // Calculate stats
        const avgSnr = validSnrs.reduce((a, b) => a + b, 0) / validSnrs.length;
        const minSnr = Math.min(...validSnrs);
        const belowThreshold = validSnrs.filter(s => s < 10).length;
        const belowThresholdPct = ((belowThreshold / validSnrs.length) * 100).toFixed(1);

        // Update stats panel
        if (statsContainer) {
            const isWarning = belowThresholdPct > 5;
            statsContainer.innerHTML = `
                <span>Avg SNR: <strong>${avgSnr.toFixed(1)}</strong></span>
                <span>Min: <strong>${minSnr.toFixed(1)}</strong></span>
                <span ${isWarning ? 'style="color: var(--danger-color);"' : ''}>Frames below 10: <strong>${belowThreshold} (${belowThresholdPct}%)</strong></span>
            `;
            statsContainer.style.display = 'flex';
        }

        // Generate narrative
        if (narrativeContainer) {
            let narrative = '';
            if (avgSnr > 20) {
                narrative = 'Strong guide star signal. Good guiding conditions.';
            } else if (avgSnr >= 10) {
                narrative = 'Adequate guide star signal. Consider a brighter guide star if RMS is elevated.';
            } else {
                narrative = 'Weak guide star signal — likely contributing to elevated RMS.';
            }
            narrativeContainer.textContent = narrative;
            narrativeContainer.style.display = 'block';
        }

        // Show chart container
        if (chartContainer) chartContainer.style.display = 'block';

        // Destroy previous chart
        if (charts.guidingSnr) charts.guidingSnr.destroy();

        // Calculate gap threshold using same logic as RMS chart
        const intervals = [];
        for (let i = 0; i < Math.min(snrData.length - 1, 50); i++) {
            intervals.push(snrData[i + 1].h - snrData[i].h);
        }
        const typicalInterval = intervals.length > 0
            ? intervals.sort((a, b) => a - b)[Math.floor(intervals.length / 2)]
            : 0.1;
        const gapThreshold = typicalInterval * 5;

        // Build dataset with gap breaks
        const chartData = [];
        for (let i = 0; i < snrData.length; i++) {
            chartData.push({ x: snrData[i].h, y: snrData[i].snr });
            // Insert null for gaps
            if (i < snrData.length - 1 && (snrData[i + 1].h - snrData[i].h) > gapThreshold) {
                chartData.push({ x: snrData[i].h + 0.001, y: null });
            }
        }

        const maxHours = snrData[snrData.length - 1].h;
        const maxSnr = Math.max(...validSnrs);

        charts.guidingSnr = new Chart(canvas, {
            type: 'line',
            data: {
                datasets: [{
                    label: 'SNR',
                    data: chartData,
                    borderColor: 'rgba(251, 191, 36, 0.9)',
                    backgroundColor: 'transparent',
                    borderWidth: 1,
                    pointRadius: 0,
                    tension: 0,
                    fill: false,
                    spanGaps: false
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                plugins: {
                    title: {
                        display: true,
                        text: 'Guide Star SNR Over Time',
                        color: dark ? COLORS.text : '#333'
                    },
                    legend: { display: false },
                    tooltip: {
                        mode: 'index',
                        intersect: false,
                        callbacks: {
                            title: function(items) {
                                if (items.length > 0) {
                                    return hoursToTime(items[0].parsed.x);
                                }
                                return '';
                            },
                            label: function(ctx) {
                                return ctx.parsed.y !== null ? `SNR: ${ctx.parsed.y.toFixed(1)}` : '';
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        type: 'linear',
                        min: 0,
                        max: maxHours,
                        title: { display: true, text: 'Time (Local)', color: dark ? COLORS.text : '#333' },
                        ticks: {
                            color: dark ? COLORS.text : '#666',
                            callback: function(value) {
                                return hoursToTime(value);
                            }
                        },
                        grid: { color: dark ? COLORS.grid : 'rgba(0, 0, 0, 0.1)' }
                    },
                    y: {
                        min: 0,
                        title: { display: true, text: 'SNR', color: dark ? COLORS.text : '#333' },
                        ticks: { color: dark ? COLORS.text : '#666' },
                        grid: { color: dark ? COLORS.grid : 'rgba(0, 0, 0, 0.1)' }
                    }
                }
            },
            plugins: [{
                id: 'snrMinOkLine',
                beforeDraw: function(chart) {
                    // Only draw if Y max > 10
                    if (chart.scales.y.max <= 10) return;

                    const ctx = chart.ctx;
                    const yAxis = chart.scales.y;
                    const xAxis = chart.scales.x;

                    // Y position for SNR = 10
                    const y = yAxis.getPixelForValue(10);
                    if (y < chart.chartArea.top || y > chart.chartArea.bottom) return;

                    ctx.save();
                    ctx.strokeStyle = COLORS.af;  // Brand warning amber
                    ctx.lineWidth = 1.5;
                    ctx.setLineDash([6, 4]);
                    ctx.beginPath();
                    ctx.moveTo(chart.chartArea.left, y);
                    ctx.lineTo(chart.chartArea.right, y);
                    ctx.stroke();

                    // Label at right end
                    ctx.fillStyle = COLORS.af;
                    ctx.font = '10px system-ui, -apple-system, sans-serif';
                    ctx.textAlign = 'right';
                    ctx.textBaseline = 'bottom';
                    ctx.fillText('Min OK', chart.chartArea.right - 4, y - 2);
                    ctx.restore();
                }
            }]
        });
    }

    /**
     * Render Dithering Tab - Settle times and success/failure
     */
    function renderDitheringTab() {
        const asiair = logData.asiair;
        const phd2 = logData.phd2;

        // Update stats
        const ditherCount = asiair?.stats?.dither_count || phd2?.stats?.dither_count || 0;
        const timeoutCount = asiair?.stats?.dither_timeout_count || phd2?.stats?.settle_timeout_count || 0;
        const successCount = ditherCount - timeoutCount;

        document.getElementById('log-dither-total').textContent = ditherCount;
        document.getElementById('log-settle-success').textContent = successCount;
        document.getElementById('log-settle-timeout').textContent = timeoutCount;

        const canvas = document.getElementById('log-dither-chart');
        if (!canvas) return;

        if (charts.dither) charts.dither.destroy();

        const dithers = asiair?.dithers || [];
        if (dithers.length === 0) {
            canvas.parentElement.innerHTML = '<p style="color: var(--text-muted); text-align: center; padding: 20px;">No dither data available.</p>';
            return;
        }

        const dark = isDarkTheme();

        charts.dither = new Chart(canvas, {
            type: 'bar',
            data: {
                labels: dithers.map((_, i) => `#${i + 1}`),
                datasets: [{
                    label: 'Settle Time (s)',
                    data: dithers.map(d => d.dur),
                    backgroundColor: dithers.map(d => d.ok ? COLORS.success : COLORS.timeout),
                    borderColor: dithers.map(d => d.ok ? COLORS.success : COLORS.timeout),
                    borderWidth: 1
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                plugins: {
                    title: {
                        display: true,
                        text: 'Dither Settle Times',
                        color: dark ? COLORS.text : '#333'
                    },
                    legend: { display: false },
                    tooltip: {
                        callbacks: {
                            afterLabel: function(context) {
                                const d = dithers[context.dataIndex];
                                return d.ok ? '✓ Success' : '✗ Timeout';
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        title: { display: true, text: 'Dither #', color: dark ? COLORS.text : '#333' },
                        ticks: { color: dark ? COLORS.text : '#666', maxTicksLimit: 20 },
                        grid: { color: dark ? COLORS.grid : 'rgba(0, 0, 0, 0.1)' }
                    },
                    y: {
                        title: { display: true, text: 'Settle Time (seconds)', color: dark ? COLORS.text : '#333' },
                        ticks: { color: dark ? COLORS.text : '#666' },
                        grid: { color: dark ? COLORS.grid : 'rgba(0, 0, 0, 0.1)' },
                        beginAtZero: true
                    }
                }
            }
        });
    }

    /**
     * Render AutoFocus Tab - Summary cards, overlay chart, drift chart, narrative, and V-curves
     */
    function renderAutoFocusTab() {
        // Support both ASIAIR and NINA log data
        let afRuns = [];

        // Debug: Log data structure
        console.log('[renderAutoFocusTab] logData.asiair:', logData.asiair);
        console.log('[renderAutoFocusTab] logData.nina:', logData.nina);

        // Check ASIAIR data
        if (logData.asiair && logData.asiair.af_runs && logData.asiair.af_runs.length > 0) {
            afRuns = logData.asiair.af_runs;
        }
        // Check NINA data (independent - use separate if, not else if)
        if (logData.nina && logData.nina.autofocus_runs && logData.nina.autofocus_runs.length > 0) {
            // Only use NINA data if ASIAIR didn't provide any runs
            if (afRuns.length === 0) {
                // Transform NINA data to match ASIAIR format for rendering
                afRuns = logData.nina.autofocus_runs.map(ninaRun => ({
                    run: ninaRun.run_index,
                    ts: ninaRun.start_time,
                    h: ninaRun.start_time ? new Date(ninaRun.start_time).getHours() : 0,
                    temp: ninaRun.temperature,
                    focus_pos: ninaRun.final_position,
                    points: ninaRun.steps.map(step => ({
                        pos: step.position,
                        sz: step.hfr !== null ? step.hfr : 999, // 999 indicates no stars
                        sigma: step.hfr_sigma
                    })),
                    // Add metadata for display
                    _nina_source: true,
                    _filter: ninaRun.filter,
                    _trigger: ninaRun.trigger,
                    _status: ninaRun.status,
                    _best_hfr: ninaRun.best_hfr,
                    _fitting_method: ninaRun.fitting_method,
                    _r_squared: ninaRun.r_squared,
                    _restored_position: ninaRun.restored_position,
                    _no_star_steps: ninaRun.no_star_steps,
                    _failure_reason: ninaRun.failure_reason
                }));
            }
        }

        if (!afRuns || afRuns.length === 0) {
            const tab = document.getElementById('log-autofocus-tab');
            if (tab) {
                tab.innerHTML = '<p style="color: var(--text-muted); text-align: center; padding: 40px;">No autofocus data available.</p>';
            }
            return;
        }
        document.getElementById('log-af-total').textContent = afRuns.length;

        // Clean up old charts
        charts.afCurves.forEach(c => c.destroy());
        charts.afCurves = [];
        if (charts.afOverlay) { charts.afOverlay.destroy(); charts.afOverlay = null; }
        if (charts.afDrift) { charts.afDrift.destroy(); charts.afDrift = null; }

        const dark = isDarkTheme();

        // --- Helper: Get color for run index ---
        function getRunColor(idx) {
            return AF_RUN_COLORS[idx % AF_RUN_COLORS.length];
        }

        // --- Helper: Find minimum star size position in points ---
        function findMinPosition(points) {
            if (!points || points.length === 0) return null;
            let minPt = points[0];
            points.forEach(p => { if (p.sz < minPt.sz) minPt = p; });
            return minPt;
        }

        // --- Helper: Get settled focus position (use focus_pos if available, else derive from points) ---
        function getSettledPosition(afRun) {
            if (afRun.focus_pos !== null && afRun.focus_pos !== undefined) {
                return afRun.focus_pos;
            }
            const minPt = findMinPosition(afRun.points);
            return minPt ? minPt.pos : null;
        }

        // --- Task 2: Summary Cards Row ---
        renderAFSummaryCards(afRuns, getRunColor, getSettledPosition);

        // --- Task 3: Overlay V-Curve Chart ---
        renderAFOverlayChart(afRuns, dark, getRunColor, getSettledPosition);

        // --- Task 4: Focus Position Drift Chart ---
        renderAFDriftChart(afRuns, dark, getSettledPosition);

        // --- Task 5: Auto-generated Narrative ---
        renderAFNarrative(afRuns, getSettledPosition);

        // --- Individual V-Curve Cards (existing functionality, now with parabola fitting) ---
        const container = document.getElementById('log-af-runs');
        if (!container) return;
        container.innerHTML = '';

        afRuns.forEach((afRun, index) => {
            // Determine success/failure status for styling
            const isSuccess = afRun._status !== 'failed';

            const card = document.createElement('div');
            card.className = 'log-af-vcurve-card';
            if (!isSuccess) card.classList.add('af-failed');

            // HEADER LINE: "Run 1 — Lum  ✓ Successful" or "Run N — Filter  ✗ Failed"
            const header = document.createElement('div');
            header.style.cssText = `
                font-size: 13px;
                font-weight: 600;
                color: var(--text-primary);
                margin-bottom: 6px;
            `;
            const filterPart = afRun._filter ? ` — ${afRun._filter}` : '';
            const statusText = isSuccess ? '✓ Successful' : '✗ Failed';
            const statusColor = isSuccess ? 'var(--nova-trend-up, #2a9060)' : '#a04040';
            header.innerHTML = `Run ${afRun.run}${filterPart} <span style="color: ${statusColor};">${statusText}</span>`;
            card.appendChild(header);

            // META LINE: timestamp · trigger (if present)
            const meta = document.createElement('div');
            meta.style.cssText = `
                font-size: 11px;
                color: var(--text-muted);
                margin-bottom: 12px;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            `;
            let metaText = afRun.ts ? new Date(afRun.ts).toLocaleTimeString() : '';
            if (afRun._trigger) metaText += (metaText ? ' · ' : '') + afRun._trigger;
            meta.textContent = metaText;
            meta.title = metaText; // Show full text on hover when truncated
            card.appendChild(meta);

            // Try parabola fit for better focus position
            let focusPos = getSettledPosition(afRun);
            let datasets = [];
            let chartMin = Infinity, chartMax = -Infinity;

            // Theme-aware grid color
            const gridColor = dark ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.05)';

            if (afRun.points && afRun.points.length > 0) {
                // Sort points by position ascending for proper left-to-right V-curve display
                const sortedSteps = [...afRun.points].sort((a, b) => a.pos - b.pos);
                const points = sortedSteps.map(p => ({
                    x: p.pos,
                    y: p.sz,
                    starCount: p.starCount || (p.sigma ? 1 : 0) // Use starCount if available
                }));
                points.forEach(p => {
                    if (p.x < chartMin) chartMin = p.x;
                    if (p.x > chartMax) chartMax = p.x;
                });

                // Find best focus point index for highlighting
                let bestFocusIdx = -1;
                const validPoints = points.filter(p => p.y < 900); // Exclude no-star points (999)
                if (validPoints.length > 0) {
                    const bestY = Math.min(...validPoints.map(p => p.y));
                    bestFocusIdx = points.findIndex(p => p.y === bestY);
                }

                const fit = fitParabola(points);
                // Chart colors based on success/failure status
                const chartColor = isSuccess ? '#83b4c5' : '#c05050';
                const bestFocusColor = isSuccess ? '#2a9060' : '#a04040';

                if (fit) {
                    const posRange = chartMax - chartMin;
                    const maxDrift = posRange * 0.2;
                    if (fit.vertex >= chartMin - maxDrift && fit.vertex <= chartMax + maxDrift) {
                        focusPos = Math.round(fit.vertex);
                    }

                    // Smooth parabola curve
                    const curvePoints = linspace(chartMin, chartMax, 100).map(x => ({ x, y: fit.fn(x) }));
                    datasets.push({
                        label: 'Fitted Curve',
                        data: curvePoints,
                        borderColor: chartColor,
                        backgroundColor: 'transparent',
                        borderWidth: 2,
                        pointRadius: 0,
                        showLine: true,
                        tension: 0,
                        fill: false
                    });

                    // Raw measurements as points with best focus highlighted
                    const pointColors = points.map((p, i) => i === bestFocusIdx ? bestFocusColor : chartColor);
                    const pointRadii = points.map((p, i) => i === bestFocusIdx ? 7 : 4);
                    const pointHoverRadii = points.map((p, i) => i === bestFocusIdx ? 8 : 6);

                    datasets.push({
                        label: 'Measured',
                        data: points,
                        borderColor: pointColors,
                        backgroundColor: pointColors,
                        borderWidth: 0,
                        pointRadius: pointRadii,
                        pointHoverRadius: pointHoverRadii,
                        showLine: false,
                        fill: false
                    });
                } else {
                    // Fallback: line connecting points
                    const sorted = [...points].sort((a, b) => a.x - b.x);
                    datasets.push({
                        label: 'Star Size (HFR)',
                        data: sorted,
                        borderColor: chartColor,
                        backgroundColor: isSuccess ? 'rgba(131, 180, 197, 0.1)' : 'rgba(192, 80, 80, 0.1)',
                        borderWidth: 2,
                        pointRadius: 4,
                        pointHoverRadius: 6,
                        tension: 0.3,
                        fill: true
                    });
                }
            }

            if (datasets.length > 0) {
                const canvas = document.createElement('canvas');
                canvas.id = `af-curve-${index}`;
                card.appendChild(canvas);

                const chart = new Chart(canvas, {
                    type: 'scatter',
                    data: { datasets: datasets },
                    options: {
                        responsive: true,
                        maintainAspectRatio: true,
                        animation: false,
                        layout: {
                            padding: { top: 8, bottom: 4 }
                        },
                        plugins: {
                            legend: { display: false },
                            tooltip: {
                                callbacks: {
                                    label: function(ctx) {
                                        const point = ctx.raw;
                                        if (point.y >= 900) {
                                            return `Pos ${ctx.parsed.x.toFixed(0)}: No stars detected`;
                                        }
                                        const hfrStr = point.y.toFixed(2);
                                        const starStr = point.starCount ? ` | Stars: ${point.starCount}` : '';
                                        return `Pos ${ctx.parsed.x.toFixed(0)}: HFR ${hfrStr}"${starStr}`;
                                    }
                                }
                            }
                        },
                        scales: {
                            x: {
                                type: 'linear',
                                reverse: false,
                                title: { display: true, text: 'Focus Position', color: dark ? COLORS.text : '#333' },
                                min: chartMin - 20,
                                max: chartMax + 20,
                                ticks: { color: dark ? COLORS.text : '#666', maxTicksLimit: 8 },
                                grid: { color: gridColor }
                            },
                            y: {
                                title: { display: true, text: 'HFR', color: dark ? COLORS.text : '#333' },
                                ticks: { color: dark ? COLORS.text : '#666' },
                                grid: { color: gridColor }
                            }
                        }
                    }
                });
                charts.afCurves.push(chart);
            } else {
                card.innerHTML += '<p style="color: var(--text-muted); font-size: 0.9em;">No V-curve points recorded.</p>';
            }

            // STATS ROW
            const statsRow = document.createElement('div');
            statsRow.style.cssText = `
                display: flex;
                flex-wrap: wrap;
                gap: 16px;
                margin-top: 12px;
            `;

            // Helper to create stat item
            const addStatItem = (label, value, valueColor = 'var(--text-primary)') => {
                const item = document.createElement('div');
                item.style.cssText = 'display: flex; flex-direction: column; gap: 2px;';
                const labelEl = document.createElement('span');
                labelEl.style.cssText = 'font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.09em; color: var(--text-faint, #999);';
                labelEl.textContent = label;
                const valueEl = document.createElement('span');
                valueEl.style.cssText = `font-size: 13px; font-weight: 600; font-family: var(--font-mono, 'DM Mono', monospace); color: ${valueColor};`;
                valueEl.textContent = value;
                item.appendChild(labelEl);
                item.appendChild(valueEl);
                statsRow.appendChild(item);
            };

            if (isSuccess) {
                // Successful stats: BEST POSITION · BEST HFR · STARS AT FOCUS · FITTING · TEMP
                // Best position
                if (afRun.focus_pos !== null && afRun.focus_pos !== undefined) {
                    addStatItem('Best Position', afRun.focus_pos, 'var(--primary-color, #83b4c5)');
                }
                // Best HFR
                if (afRun._best_hfr !== null && afRun._best_hfr !== undefined) {
                    addStatItem('Best HFR', afRun._best_hfr.toFixed(2) + '"');
                }
                // Stars at focus (no_star_steps - but for successful runs, this could be stars count at best position)
                if (afRun._no_star_steps !== null && afRun._no_star_steps !== undefined) {
                    // For successful runs, this may represent star count at focus if available
                    addStatItem('Stars at Focus', afRun._no_star_steps);
                }
                // Fitting method
                if (afRun._fitting_method) {
                    addStatItem('Fitting', afRun._fitting_method);
                }
                // Temperature
                if (afRun.temp !== null && afRun.temp !== undefined) {
                    addStatItem('Temp', afRun.temp.toFixed(1) + '°C');
                }
            } else {
                // Failed stats: RESTORED TO · R² · THRESHOLD · NO-STAR STEPS
                // Restored to (focus_pos on failed runs)
                if (afRun.focus_pos !== null && afRun.focus_pos !== undefined) {
                    addStatItem('Restored To', afRun.focus_pos);
                }
                // R²
                if (afRun._r_squared !== null && afRun._r_squared !== undefined) {
                    const rSquaredColor = afRun._r_squared < 0 ? '#a04040' : 'var(--text-primary)';
                    addStatItem('R²', afRun._r_squared.toFixed(3), rSquaredColor);
                }
                // R² threshold
                if (afRun._r_squared_threshold !== null && afRun._r_squared_threshold !== undefined) {
                    addStatItem('Threshold', afRun._r_squared_threshold);
                }
                // No-star steps
                if (afRun._no_star_steps > 0) {
                    addStatItem('No-Star Steps', afRun._no_star_steps);
                }
            }

            if (statsRow.children.length > 0) {
                card.appendChild(statsRow);
            }

            // FAILURE EXPLANATION BOX — failed cards only
            if (!isSuccess) {
                const explanation = document.createElement('div');
                explanation.className = 'log-af-failure-explanation';

                let explanationText = '';
                if (afRun._r_squared !== null && afRun._r_squared !== undefined) {
                    explanationText += `R² below threshold (${afRun._r_squared.toFixed(3)} / ${afRun._r_squared_threshold || 'N/A'})`;
                    if (afRun._fitting_method) {
                        explanationText += ` — ${afRun._fitting_method} fit failed.`;
                    } else {
                        explanationText += '.';
                    }
                    if (afRun._no_star_steps > 0) {
                        explanationText += ` ${afRun._no_star_steps} step${afRun._no_star_steps > 1 ? 's' : ''} had no star detections.`;
                    }
                    if (afRun.focus_pos !== null && afRun.focus_pos !== undefined) {
                        explanationText += ` Focuser restored to previous position ${afRun.focus_pos}.`;
                    }
                } else if (afRun._failure_reason) {
                    explanationText = afRun._failure_reason;
                } else {
                    explanationText = 'Autofocus failed. See log for details.';
                }

                explanation.textContent = explanationText;
                card.appendChild(explanation);
            }

            container.appendChild(card);
        });
    }

    /**
     * Task 2: Render AF Summary Cards
     * Summary bar at top with: Successful / Failed / Final Position / Avg HFR
     * Row 2: AF Run Cards with colored positions
     */
    function renderAFSummaryCards(afRuns, getRunColor, getSettledPosition) {
        const primaryContainer = document.getElementById('log-af-summary-primary');
        const runsContainer = document.getElementById('log-af-summary-cards');
        if (!primaryContainer || !runsContainer) return;
        primaryContainer.innerHTML = '';
        runsContainer.innerHTML = '';

        // Calculate summary stats
        const successfulRuns = afRuns.filter(r => r._status !== 'failed');
        const failedRuns = afRuns.filter(r => r._status === 'failed');
        const positions = afRuns.map(r => getSettledPosition(r)).filter(p => p !== null);
        const finalPosition = positions.length > 0 ? positions[positions.length - 1] : null;

        // Calculate average HFR from successful runs
        const hfrValues = successfulRuns
            .filter(r => r._best_hfr !== null && r._best_hfr !== undefined)
            .map(r => r._best_hfr);
        const avgHfr = hfrValues.length > 0
            ? hfrValues.reduce((a, b) => a + b, 0) / hfrValues.length
            : null;

        // === SUMMARY BAR ===
        // Style helper for inline stat items
        const createSummaryItem = (label, value, valueColor = 'var(--text-primary)') => {
            const item = document.createElement('div');
            item.style.cssText = 'display: flex; flex-direction: column; gap: 2px;';
            const labelEl = document.createElement('span');
            labelEl.style.cssText = 'font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.09em; color: var(--text-faint, #999);';
            labelEl.textContent = label;
            const valueEl = document.createElement('span');
            valueEl.style.cssText = `font-size: 13px; font-weight: 600; font-family: var(--font-mono, 'DM Mono', monospace); color: ${valueColor};`;
            valueEl.textContent = value;
            item.appendChild(labelEl);
            item.appendChild(valueEl);
            return item;
        };

        // Summary bar container
        const summaryBar = document.createElement('div');
        summaryBar.style.cssText = `
            display: flex;
            gap: 24px;
            flex-wrap: wrap;
            padding-bottom: 16px;
            border-bottom: 1px solid var(--border-light, #e5e5e5);
            margin-bottom: 20px;
            max-width: 970px;
        `;

        // Successful count
        summaryBar.appendChild(createSummaryItem('Successful', successfulRuns.length, '#4a9e6e'));
        // Failed count
        if (failedRuns.length > 0) {
            summaryBar.appendChild(createSummaryItem('Failed', failedRuns.length, '#c05050'));
        }
        // Final position
        if (finalPosition !== null) {
            summaryBar.appendChild(createSummaryItem('Final Pos', finalPosition, 'var(--primary-color)'));
        }
        // Average HFR
        if (avgHfr !== null) {
            summaryBar.appendChild(createSummaryItem('Avg HFR', avgHfr.toFixed(2) + '"', 'var(--primary-color)'));
        }
        // Total runs
        summaryBar.appendChild(createSummaryItem('Total', afRuns.length));

        primaryContainer.appendChild(summaryBar);

        // === ROW 2: AF Run Cards ===
        afRuns.forEach((afRun, idx) => {
            const isSuccess = afRun._status !== 'failed';
            const color = isSuccess ? '#4a9e6e' : '#c05050';
            const focusPos = getSettledPosition(afRun);
            const card = document.createElement('div');
            card.className = 'log-af-stat-card';

            let subtitle = '';
            if (afRun.temp !== null && afRun.temp !== undefined && focusPos !== null) {
                subtitle = `${afRun.temp.toFixed(1)}°C | pos ${focusPos}`;
            } else if (focusPos !== null) {
                subtitle = `pos ${focusPos}`;
            } else if (afRun.temp !== null && afRun.temp !== undefined) {
                subtitle = `${afRun.temp.toFixed(1)}°C`;
            }
            if (!isSuccess && afRun._failure_reason) {
                subtitle = subtitle ? subtitle + ' | Failed' : 'Failed';
            }

            card.innerHTML = `
                <div class="stat-value" style="color: ${color};">${focusPos !== null ? focusPos : '—'}</div>
                <div class="stat-label">AF Run ${afRun.run}</div>
                ${subtitle ? `<div class="stat-subtitle">${subtitle}</div>` : ''}
            `;
            runsContainer.appendChild(card);
        });

        // === TOTALS BAR ===
        const totalsBar = document.createElement('div');
        totalsBar.style.cssText = `
            background: var(--bg-tertiary, var(--bg-light-gray, #f5f5f5));
            border-radius: 8px;
            padding: 12px 14px;
            margin-top: 16px;
            font-size: 12px;
            color: var(--text-muted);
            display: flex;
            flex-wrap: wrap;
            gap: 16px;
            max-width: 970px;
        `;

        // Helper to add totals item
        const addTotalsItem = (label, value, valueColor = 'var(--text-primary)') => {
            const span = document.createElement('span');
            span.innerHTML = `${label}: <span style="font-family: var(--font-mono, 'DM Mono', monospace); font-weight: 600; color: ${valueColor};">${value}</span>`;
            totalsBar.appendChild(span);
        };

        // Session timespan
        if (afRuns.length > 1 && afRuns[0].ts && afRuns[afRuns.length - 1].ts) {
            const start = new Date(afRuns[0].ts);
            const end = new Date(afRuns[afRuns.length - 1].ts);
            const duration = Math.round((end - start) / 60000); // minutes
            const hours = Math.floor(duration / 60);
            const mins = duration % 60;
            addTotalsItem('Duration', hours > 0 ? `${hours}h ${mins}m` : `${mins}m`);
        }

        // Temperature range
        const temps = afRuns.filter(r => r.temp !== null && r.temp !== undefined).map(r => r.temp);
        if (temps.length >= 2) {
            const minT = Math.min(...temps).toFixed(1);
            const maxT = Math.max(...temps).toFixed(1);
            addTotalsItem('Temp Range', `${minT}°C to ${maxT}°C`);
        }

        // Position range
        if (positions.length >= 2) {
            const minP = Math.min(...positions);
            const maxP = Math.max(...positions);
            const shift = maxP - minP;
            addTotalsItem('Focus Range', `${minP} - ${maxP} (${shift} shift)`, 'var(--primary-color)');
        }

        // Average HFR in totals
        if (avgHfr !== null) {
            addTotalsItem('Avg HFR', avgHfr.toFixed(2) + '"', 'var(--primary-color)');
        }

        // Failure rate
        if (failedRuns.length > 0) {
            const failRate = ((failedRuns.length / afRuns.length) * 100).toFixed(0);
            addTotalsItem('Fail Rate', `${failRate}%`, '#c05050');
        }

        if (totalsBar.children.length > 0) {
            runsContainer.parentElement.insertBefore(totalsBar, runsContainer.nextSibling);
        }
    }

    /**
     * Task 3: Render Overlay V-Curve Chart with parabola fitting and vertical focus lines
     */
    function renderAFOverlayChart(afRuns, dark, getRunColor, getSettledPosition) {
        const section = document.getElementById('log-af-overlay-section');
        const canvas = document.getElementById('logAfOverlayChart');
        if (!section || !canvas || afRuns.length === 0) return;

        // Check if any run has points
        const hasPoints = afRuns.some(r => r.points && r.points.length > 0);
        if (!hasPoints) {
            section.style.display = 'none';
            return;
        }
        section.style.display = 'block';

        // Calculate global min/max position across all runs
        let globalMinPos = Infinity, globalMaxPos = -Infinity;
        afRuns.forEach(afRun => {
            if (afRun.points && afRun.points.length > 0) {
                afRun.points.forEach(p => {
                    if (p.pos < globalMinPos) globalMinPos = p.pos;
                    if (p.pos > globalMaxPos) globalMaxPos = p.pos;
                });
            }
        });

        // Store fitted focus positions for summary cards and focus lines
        const fittedPositions = new Map();

        // Build datasets with parabola fitting
        const datasets = [];
        const focusLines = [];

        afRuns.forEach((afRun, idx) => {
            const color = getRunColor(idx);
            const tempStr = (afRun.temp !== null && afRun.temp !== undefined) ? `${afRun.temp.toFixed(1)}°C` : '';
            const label = tempStr ? `AF Run ${afRun.run} (${tempStr})` : `AF Run ${afRun.run}`;

            if (afRun.points && afRun.points.length > 0) {
                const points = afRun.points.map(p => ({ x: p.pos, y: p.sz }));
                const fit = fitParabola(points);

                // Determine focus position: use fitted vertex if valid, else raw minimum
                let focusPos = null;
                if (fit) {
                    const posRange = globalMaxPos - globalMinPos;
                    const maxDrift = posRange * 0.2;
                    // Only use fitted vertex if it's within 20% of measured range
                    if (fit.vertex >= globalMinPos - maxDrift && fit.vertex <= globalMaxPos + maxDrift) {
                        focusPos = Math.round(fit.vertex);
                    }
                }
                if (focusPos === null) {
                    focusPos = getSettledPosition(afRun);
                }
                fittedPositions.set(idx, focusPos);

                if (fit && focusPos !== null) {
                    // Use parabola fit: smooth curve + scatter overlay
                    const minP = Math.min(...points.map(p => p.x));
                    const maxP = Math.max(...points.map(p => p.x));
                    const curvePoints = linspace(minP, maxP, 150).map(x => ({ x, y: fit.fn(x) }));

                    // Smooth curve (no points)
                    datasets.push({
                        label: label,
                        data: curvePoints,
                        borderColor: color,
                        backgroundColor: 'transparent',
                        borderWidth: 2,
                        pointRadius: 0,
                        showLine: true,
                        tension: 0,
                        fill: false,
                        _isCurve: true
                    });

                    // Raw measurements overlay (points only, no line)
                    datasets.push({
                        label: label + ' (measured)',
                        data: points,
                        borderColor: color,
                        backgroundColor: color,
                        borderWidth: 0,
                        pointRadius: 5,
                        pointHoverRadius: 7,
                        showLine: false,
                        fill: false,
                        _isPoints: true
                    });
                } else {
                    // Fallback: connect dots (no fit possible)
                    const sortedPoints = [...points].sort((a, b) => a.x - b.x);
                    datasets.push({
                        label: label,
                        data: sortedPoints,
                        borderColor: color,
                        backgroundColor: color + '33',
                        borderWidth: 2,
                        pointRadius: 5,
                        pointHoverRadius: 7,
                        showLine: true,
                        tension: 0.3,
                        fill: false
                    });
                }

                // Collect focus line data
                if (focusPos !== null) {
                    focusLines.push({ pos: focusPos, color: color, run: afRun.run });
                }
            }
        });

        // Create chart with beforeDraw plugin for vertical focus lines
        charts.afOverlay = new Chart(canvas, {
            type: 'scatter',
            data: { datasets: datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                layout: {
                    padding: { top: 20 }
                },
                plugins: {
                    legend: {
                        display: true,
                        position: 'top',
                        labels: {
                            color: dark ? COLORS.text : '#333',
                            usePointStyle: true,
                            pointStyle: 'circle',
                            filter: function(item) {
                                // Hide "(measured)" labels, only show main curve labels
                                return !item.text.includes('(measured)');
                            }
                        }
                    },
                    tooltip: {
                        callbacks: {
                            label: function(ctx) {
                                const label = ctx.dataset.label.replace(' (measured)', '');
                                return `${label}: pos ${ctx.parsed.x.toFixed(0)}, size ${ctx.parsed.y.toFixed(2)}`;
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        type: 'linear',
                        title: { display: true, text: 'EAF Position (steps)', color: dark ? COLORS.text : '#333' },
                        min: globalMinPos - 50,
                        max: globalMaxPos + 50,
                        ticks: { color: dark ? COLORS.text : '#666' },
                        grid: { color: dark ? COLORS.grid : 'rgba(0, 0, 0, 0.1)' }
                    },
                    y: {
                        title: { display: true, text: 'Star Size (HFR px)', color: dark ? COLORS.text : '#333' },
                        ticks: { color: dark ? COLORS.text : '#666' },
                        grid: { color: dark ? COLORS.grid : 'rgba(0, 0, 0, 0.1)' }
                    }
                }
            },
            plugins: [{
                id: 'focusLinePlugin',
                beforeDraw: function(chart) {
                    const ctx = chart.ctx;
                    const xAxis = chart.scales.x;
                    const labelY = chart.chartArea.bottom - 12;  // Inside chart, above X axis
                    ctx.font = '10px system-ui, -apple-system, sans-serif';
                    ctx.textAlign = 'center';
                    ctx.textBaseline = 'bottom';

                    focusLines.forEach(fl => {
                        const x = xAxis.getPixelForValue(fl.pos);
                        if (x >= chart.chartArea.left && x <= chart.chartArea.right) {
                            ctx.save();
                            // Draw vertical dashed line
                            ctx.strokeStyle = fl.color;
                            ctx.lineWidth = 2;
                            ctx.setLineDash([6, 4]);
                            ctx.beginPath();
                            ctx.moveTo(x, chart.chartArea.top);
                            ctx.lineTo(x, chart.chartArea.bottom);
                            ctx.stroke();

                            // Draw label background (white with opacity)
                            const labelText = `R${fl.run}`;
                            const labelWidth = ctx.measureText(labelText).width;
                            ctx.fillStyle = 'rgba(255, 255, 255, 0.85)';
                            ctx.fillRect(x - labelWidth / 2 - 2, labelY - 12, labelWidth + 4, 12);

                            // Draw label text
                            ctx.fillStyle = fl.color;
                            ctx.fillText(labelText, x, labelY);
                            ctx.restore();
                        }
                    });
                }
            }]
        });

        // Return fitted positions for use by summary cards
        return fittedPositions;
    }

    /**
     * Task 4: Render Focus Position Drift Chart
     */
    function renderAFDriftChart(afRuns, dark, getSettledPosition) {
        const section = document.getElementById('log-af-drift-section');
        const canvas = document.getElementById('logAfDriftChart');
        if (!section || !canvas || afRuns.length < 2) {
            if (section) section.style.display = 'none';
            return;
        }

        const positions = afRuns.map(r => getSettledPosition(r)).filter(p => p !== null);
        if (positions.length < 2) {
            section.style.display = 'none';
            return;
        }
        section.style.display = 'block';

        // Group runs by filter
        // ASIAIR: _filter is undefined → group as "All"
        // NINA: _filter may be null (Unknown) or a filter name
        const filterGroups = new Map();
        afRuns.forEach((run, idx) => {
            const filter = run._filter === undefined ? 'All' : (run._filter || 'Unknown');
            if (!filterGroups.has(filter)) {
                filterGroups.set(filter, []);
            }
            filterGroups.get(filter).push({ run, idx });
        });

        // Assign colors: Nova teal primary (#83b4c5) for first, then AF_RUN_COLORS
        const filterColors = ['#83b4c5', ...AF_RUN_COLORS];
        let colorIndex = 0;

        // Build labels and datasets per filter
        const labels = afRuns.map(r => `Run ${r.run}`);
        const datasets = [];
        const minPos = Math.min(...positions);
        const maxPos = Math.max(...positions);
        const padding = 50;

        filterGroups.forEach((runs, filterName) => {
            // Determine color for this filter
            const color = filterColors[colorIndex % filterColors.length];
            colorIndex++;

            // Build data array with nulls for positions not in this filter group
            const data = new Array(afRuns.length).fill(null);
            runs.forEach(({ run, idx }) => {
                const pos = getSettledPosition(run);
                data[idx] = pos;
            });

            datasets.push({
                label: filterName,
                data: data,
                borderColor: color,
                backgroundColor: color + '33',
                borderWidth: 3,
                pointRadius: 8,
                pointHoverRadius: 10,
                pointBackgroundColor: color,
                tension: 0.2,
                fill: false,
                spanGaps: false // Don't connect points across filter groups
            });
        });

        charts.afDrift = new Chart(canvas, {
            type: 'line',
            data: {
                labels: labels,
                datasets: datasets
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                plugins: {
                    legend: {
                        display: true,
                        position: 'top',
                        labels: {
                            color: dark ? COLORS.text : '#333',
                            usePointStyle: true,
                            padding: 15
                        }
                    },
                    tooltip: {
                        callbacks: {
                            label: function(ctx) {
                                const val = ctx.parsed.y;
                                const filterLabel = ctx.dataset.label;
                                return val !== null ? `${filterLabel}: EAF ${val}` : null;
                            }
                        }
                    }
                },
                scales: {
                    x: {
                        title: { display: false },
                        ticks: { color: dark ? COLORS.text : '#666' },
                        grid: { color: dark ? COLORS.grid : 'rgba(0, 0, 0, 0.1)' }
                    },
                    y: {
                        title: { display: true, text: 'EAF Position (steps)', color: dark ? COLORS.text : '#333' },
                        min: minPos - padding,
                        max: maxPos + padding,
                        ticks: { color: dark ? COLORS.text : '#666' },
                        grid: { color: dark ? COLORS.grid : 'rgba(0, 0, 0, 0.1)' }
                    }
                }
            }
        });
    }

    /**
     * Task 5: Generate and render AF narrative text
     */
    function renderAFNarrative(afRuns, getSettledPosition) {
        const container = document.getElementById('log-af-narrative');
        if (!container || afRuns.length < 2) {
            if (container) container.style.display = 'none';
            return;
        }

        const positions = afRuns.map(r => getSettledPosition(r));
        const temps = afRuns.filter(r => r.temp !== null && r.temp !== undefined).map(r => r.temp);

        // Check for linearity (monotonically increasing or decreasing)
        let isLinear = true;
        let direction = 0;
        for (let i = 1; i < positions.length; i++) {
            if (positions[i] === null || positions[i - 1] === null) continue;
            const diff = positions[i] - positions[i - 1];
            if (diff !== 0) {
                if (direction === 0) direction = Math.sign(diff);
                else if (Math.sign(diff) !== direction) { isLinear = false; break; }
            }
        }
        const linearStr = isLinear ? 'linear' : 'non-linear';
        const causeStr = isLinear ? 'likely thermal drift' : 'atmospheric or equipment changes';

        // Build position chain string
        const posStr = positions.map(p => p !== null ? `~${p}` : '?').join(' → ');

        // Time span
        const hours = afRuns.length > 1 && afRuns[afRuns.length - 1].h && afRuns[0].h
            ? (afRuns[afRuns.length - 1].h - afRuns[0].h).toFixed(1)
            : '?';

        // Temp drift and steps/°C
        let tempDriftStr = '';
        let stepsPerDegreeStr = '';
        if (temps.length >= 2) {
            const tempDrift = temps[temps.length - 1] - temps[0];
            tempDriftStr = `${tempDrift.toFixed(1)}°C temp drop over ${hours}h.`;

            const focusShift = positions[positions.length - 1] - positions[0];
            if (tempDrift !== 0 && focusShift !== null) {
                const stepsPerDeg = Math.abs(focusShift / tempDrift).toFixed(0);
                stepsPerDegreeStr = `~${stepsPerDeg} steps/°C compensation factor visible in the data.`;
            }
        }

        // Anomaly detection: any run's min star size > 20% worse than run 1
        let anomalyStr = '';
        const minPt0 = afRuns[0].points && afRuns[0].points.length > 0
            ? findMinStarSize(afRuns[0].points)
            : null;
        if (minPt0 !== null) {
            for (let i = 1; i < afRuns.length; i++) {
                const minPti = afRuns[i].points && afRuns[i].points.length > 0
                    ? findMinStarSize(afRuns[i].points)
                    : null;
                if (minPti !== null && minPti > minPt0 * 1.2) {
                    anomalyStr = `AF run ${afRuns[i].run} shows degraded seeing (min star size ${minPti.toFixed(2)}). `;
                    break;
                }
            }
        }

        // Build narrative
        const narrative = `Focus shifted from ${posStr} (${linearStr}, ${causeStr}). ${tempDriftStr} ${stepsPerDegreeStr} ${anomalyStr}`.replace(/\s+/g, ' ').trim();
        container.textContent = narrative;
        container.style.display = 'block';
    }

    /**
     * Helper: Find minimum star size from points array
     */
    function findMinStarSize(points) {
        if (!points || points.length === 0) return null;
        return Math.min(...points.map(p => p.sz));
    }

    /**
     * Render NINA Event Log Tab
    /**
     * Render NINA Event Log Tab
     * Redesigned phase-based layout with equipment, guiding, imaging, and flats groups.
     */
    function renderNinaTab() {
        const tabBtn = document.getElementById('nina-tab-btn');
        const ninaData = logData && logData.nina;

        // Hide tab if no NINA data
        if (!ninaData || (!ninaData.timeline_phases && !ninaData.equipment_events && !ninaData.guiding_events)) {
            if (tabBtn) tabBtn.style.display = 'none';
            return;
        }

        // Show tab button
        if (tabBtn) tabBtn.style.display = '';

        // Render stats bar
        renderNinaStatsBar(ninaData);

        // Render equipment line (max 3 items)
        renderNinaEquipmentLine(ninaData);

        // Render collapsible phase groups
        renderNinaPhaseGroups(ninaData);

        // Render totals bar
        renderNinaTotalsBar(ninaData);

        // Setup expand/collapse handlers
        setupNinaExpandCollapse();
    }

    /**
     * Render stats bar (version, session times, error/warning counts)
     */
    function renderNinaStatsBar(ninaData) {
        const container = document.getElementById('log-nina-header');
        if (!container) return;
        container.innerHTML = '';

        // Helper to create item
        const addItem = (label, value, valueClass = '') => {
            const item = document.createElement('div');
            item.className = 'log-nina-header-item';
            item.innerHTML = `
                <span class="log-nina-header-label">${label}</span>
                <span class="log-nina-header-value ${valueClass}">${value}</span>
            `;
            container.appendChild(item);
        };

        // NINA version
        if (ninaData.nina_version) {
            addItem('NINA', ninaData.nina_version);
        }

        // Session timespan
        if (ninaData.session_start && ninaData.session_end) {
            const start = new Date(ninaData.session_start);
            const end = new Date(ninaData.session_end);
            const duration = Math.round((end - start) / 60000);
            const hours = Math.floor(duration / 60);
            const mins = duration % 60;
            const startStr = start.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
            const endStr = end.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
            // Add +1 for end time if it spans past midnight
            const endMarker = end.getDate() > start.getDate() ? '+1' : '';
            addItem('Session', `${startStr} -> ${endStr}${endMarker}`);
        }

        // Error count (use error_events if available, fall back to legacy errors)
        const errorCount = ninaData.error_events ? ninaData.error_events.length : (ninaData.errors ? ninaData.errors.length : 0);
        if (errorCount > 0) {
            addItem('Errors', errorCount, 'red');
        }

        // Warning count (use warning_events if available, fall back to legacy warnings)
        const warningCount = ninaData.warning_events ? ninaData.warning_events.length : (ninaData.warnings ? ninaData.warnings.length : 0);
        if (warningCount > 0) {
            addItem('Warnings', warningCount, 'amber');
        }
    }

    /**
     * Render equipment line (camera, mount, guider - max 3)
     */
    function renderNinaEquipmentLine(ninaData) {
        const container = document.getElementById('log-nina-equipment');
        if (!container) return;
        container.innerHTML = '';

        const eq = ninaData.equipment || {};
        const items = [];

        // Priority order: camera, mount, guider
        if (eq.camera) items.push(['Camera', eq.camera]);
        if (eq.mount) items.push(['Mount', eq.mount]);
        if (eq.guider) items.push(['Guider', eq.guider]);

        if (items.length === 0) {
            container.style.display = 'none';
            return;
        }

        // Only show first 3 items
        const displayItems = items.slice(0, 3);
        container.style.display = 'flex';

        displayItems.forEach(([label, value]) => {
            const item = document.createElement('div');
            item.className = 'log-nina-equipment-item';
            item.innerHTML = `
                <span class="log-nina-equipment-label">${label}:</span>
                <span class="log-nina-equipment-value">${value}</span>
            `;
            container.appendChild(item);
        });
    }

    /**
     * Render collapsible phase groups
     */
    function renderNinaPhaseGroups(ninaData) {
        const container = document.getElementById('log-nina-timeline');
        if (!container) return;
        container.innerHTML = '';

        // Helper to render a phase group
        const renderPhaseGroup = (phaseId, badgeClass, title, events, timeRange, errorCount = 0) => {
            if (!events || events.length === 0) return;

            const phaseEl = document.createElement('div');
            phaseEl.className = 'log-nina-phase';
            phaseEl.dataset.phaseId = phaseId;

            // Calculate timespan
            let timespan = '';
            if (timeRange.start && timeRange.end) {
                const start = new Date(timeRange.start);
                const end = new Date(timeRange.end);
                const duration = Math.round((end - start) / 60000);
                const mins = duration % 60;
                const hours = Math.floor(duration / 60);
                timespan = hours > 0 ? `${hours}h ${mins}m` : `${mins}m`;
            }

            // Build header
            const header = document.createElement('div');
            header.className = 'log-nina-phase-header';

            // Left side: badge + title
            const leftSide = document.createElement('div');
            leftSide.className = 'log-nina-phase-left';

            const badge = document.createElement('span');
            badge.className = `log-nina-phase-badge ${badgeClass}`;
            badge.textContent = phaseId;
            leftSide.appendChild(badge);

            const titleEl = document.createElement('span');
            titleEl.className = 'log-nina-phase-title';
            titleEl.textContent = title;
            leftSide.appendChild(titleEl);

            header.appendChild(leftSide);

            // Right side: counts, timespan, chevron
            const rightSide = document.createElement('div');
            rightSide.className = 'log-nina-phase-right';

            if (errorCount > 0) {
                const errPill = document.createElement('span');
                errPill.className = 'log-nina-phase-count errors';
                errPill.textContent = errorCount + ' err';
                rightSide.appendChild(errPill);
            }

            if (timespan) {
                const timeSpan = document.createElement('span');
                timeSpan.className = 'log-nina-phase-timespan';
                timeSpan.textContent = timespan;
                rightSide.appendChild(timeSpan);
            }

            const chevron = document.createElement('span');
            chevron.className = 'log-nina-phase-chevron';
            chevron.textContent = '▶';
            rightSide.appendChild(chevron);

            header.appendChild(rightSide);
            phaseEl.appendChild(header);

            // Phase body (events list) - collapsed by default
            const body = document.createElement('div');
            body.className = 'log-nina-phase-body';

            events.forEach(event => {
                const eventEl = document.createElement('div');
                const level = event.level || 'info';
                eventEl.className = `log-nina-event ${level}`;

                // Timestamp
                const timeEl = document.createElement('span');
                timeEl.className = 'log-nina-event-time';
                if (event.time) {
                    const date = new Date(event.time);
                    timeEl.textContent = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
                }
                eventEl.appendChild(timeEl);

                // Icon
                const iconEl = document.createElement('span');
                iconEl.className = `log-nina-event-icon ${level}`;
                if (level === 'error') {
                    iconEl.textContent = '✕';
                } else if (level === 'warning') {
                    iconEl.textContent = '⚠';
                } else if (event.message && event.message.includes('connected')) {
                    iconEl.textContent = '✓';
                } else if (event.message && event.message.startsWith('Starting') || event.message.startsWith('▶')) {
                    iconEl.textContent = '▶';
                } else {
                    iconEl.textContent = '○';
                }
                eventEl.appendChild(iconEl);

                // Message
                const msgEl = document.createElement('span');
                msgEl.className = 'log-nina-event-message';
                msgEl.textContent = event.message + (event.count ? ` (${event.count}×)` : '');
                eventEl.appendChild(msgEl);

                body.appendChild(eventEl);
            });

            phaseEl.appendChild(body);

            // Toggle body on header click
            header.addEventListener('click', () => {
                const isExpanded = phaseEl.classList.contains('expanded');
                phaseEl.classList.toggle('expanded');
                body.style.display = isExpanded ? 'none' : 'block';
            });

            container.appendChild(phaseEl);
        };

        // GROUP 1: STARTUP (Equipment Connection)
        if (ninaData.equipment_events && ninaData.equipment_events.length > 0) {
            const firstTime = ninaData.equipment_events[0]?.time;
            // Only include equipment events within 30 minutes of first connection (ignore reconnections)
            const thirtyMinutesMs = 30 * 60 * 1000;
            const startupWindowEnd = new Date(new Date(firstTime).getTime() + thirtyMinutesMs);
            const initialEvents = ninaData.equipment_events.filter(e => new Date(e.time) <= startupWindowEnd);
            const startupEvents = initialEvents.map(e => ({
                time: e.time,
                level: 'info',
                message: `✓ ${e.device_type} connected - ${e.device_id}`
            }));
            const lastStartupTime = initialEvents[initialEvents.length - 1]?.time || firstTime;
            renderPhaseGroup('startup', 'startup', 'Equipment Connection', startupEvents, { start: firstTime, end: lastStartupTime });
        }

        // GROUP 2: SEQUENCE (if available in timeline_phases)
        const sequencePhases = ninaData.timeline_phases?.filter(p => p.badge_class === 'sequence');
        if (sequencePhases && sequencePhases.length > 0) {
            renderPhaseGroup('sequence', 'sequence', 'Sequence Started',
                [{ time: sequencePhases[0].start_time, level: 'info', message: '▶ Advanced sequence started' }],
                { start: sequencePhases[0].start_time, end: sequencePhases[sequencePhases.length - 1].end_time }
            );
        }

        // GROUP 3: GUIDING (guiding_events)
        if (ninaData.guiding_events && ninaData.guiding_events.length > 0) {
            const firstTime = ninaData.guiding_events[0]?.time;
            const lastTime = ninaData.guiding_events[ninaData.guiding_events.length - 1]?.time;
            const errorCount = ninaData.guiding_events.filter(e => e.level === 'error').length;
            renderPhaseGroup('guiding', 'guiding', 'Guiding Setup',
                ninaData.guiding_events,
                { start: firstTime, end: lastTime },
                errorCount
            );
        }

        // GROUP 4: IMAGING (timeline_phases imaging events + imaging_summary)
        const imagingPhases = ninaData.timeline_phases?.filter(p => p.badge_class === 'imaging');
        if (imagingPhases && imagingPhases.length > 0) {
            const imagingEvents = [];

            // Add summary event first
            if (ninaData.imaging_summary) {
                const summary = ninaData.imaging_summary;
                const filtersStr = summary.filters_used && summary.filters_used.length > 0
                    ? summary.filters_used.join(' · ')
                    : 'none';
                imagingEvents.push({
                    time: imagingPhases[0].start_time,
                    level: 'info',
                    message: `▶ Sequence started - ${filtersStr} · ${summary.gain}G · ${summary.binning}`
                });
            }

            // Get imaging time range for filtering events
            const firstImagingTime = imagingPhases[0]?.start_time;
            const lastImagingTime = imagingPhases[imagingPhases.length - 1]?.end_time;

            // Add error events that occurred during imaging (skip ASI control warnings)
            if (ninaData.error_events) {
                ninaData.error_events.forEach(e => {
                    if (e.time && e.time >= firstImagingTime && e.time <= lastImagingTime) {
                        imagingEvents.push({
                            time: e.time,
                            level: 'error',
                            message: e.message,
                            count: e.count
                        });
                    }
                });
            }

            // Add warning events that occurred during imaging (skip ASI control warnings)
            if (ninaData.warning_events) {
                ninaData.warning_events.forEach(e => {
                    if (e.time && e.time >= firstImagingTime && e.time <= lastImagingTime) {
                        // Skip ASI control value warnings (already collapsed as non-critical)
                        if (e.message.includes('Camera control values not supported')) {
                            return;
                        }
                        imagingEvents.push({
                            time: e.time,
                            level: 'warning',
                            message: e.message,
                            count: e.count
                        });
                    }
                });
            }

            const errorCount = imagingEvents.filter(e => e.level === 'error').length;
            const imagingTitle = ninaData.imaging_summary?.filters_used?.length > 0
                ? `Light Acquisition - ${ninaData.imaging_summary.filters_used.join(' · ')}`
                : 'Light Acquisition';

            renderPhaseGroup('imaging', 'imaging', imagingTitle,
                imagingEvents,
                { start: firstImagingTime, end: lastImagingTime },
                errorCount
            );
        }

        // GROUP 5: FLATS (flat_events)
        if (ninaData.flat_events && ninaData.flat_events.length > 0) {
            // Deduplicate flat events - group by message and keep only unique messages
            const seenMessages = new Set();
            const uniqueFlatEvents = ninaData.flat_events.filter(e => {
                if (seenMessages.has(e.message)) {
                    return false;
                }
                seenMessages.add(e.message);
                return true;
            });

            const firstTime = ninaData.flat_events[0]?.time;
            const lastTime = ninaData.flat_events[ninaData.flat_events.length - 1]?.time;
            renderPhaseGroup('flats', 'flats', 'Flat Frames',
                uniqueFlatEvents,
                { start: firstTime, end: lastTime }
            );
        }
    }

    /**
     * Render totals bar
     */
    function renderNinaTotalsBar(ninaData) {
        const container = document.getElementById('log-nina-totals');
        if (!container) return;
        container.innerHTML = '';

        // Helper to add item
        const addItem = (label, value) => {
            const span = document.createElement('span');
            span.innerHTML = `${label}: <span class="value">${value}</span>`;
            container.appendChild(span);
        };

        // Duration
        if (ninaData.session_start && ninaData.session_end) {
            const start = new Date(ninaData.session_start);
            const end = new Date(ninaData.session_end);
            const duration = Math.round((end - start) / 60000);
            const hours = Math.floor(duration / 60);
            const mins = duration % 60;
            addItem('Duration', hours > 0 ? `${hours}h ${mins}m` : `${mins}m`);
        }

        // Filters (from imaging_summary)
        if (ninaData.imaging_summary?.filters_used?.length > 0) {
            addItem('Filters', ninaData.imaging_summary.filters_used.join(' · '));
        }

        // AutoFocus runs
        if (ninaData.autofocus_runs && ninaData.autofocus_runs.length > 0) {
            const successCount = ninaData.autofocus_runs.filter(r => r.status === 'success').length;
            const failCount = ninaData.autofocus_runs.length - successCount;
            addItem('AutoFocus', `${ninaData.autofocus_runs.length} (${successCount} ✓ · ${failCount} ✕)`);
        }

        // Error count
        const errorCount = ninaData.error_events ? ninaData.error_events.length : (ninaData.errors ? ninaData.errors.length : 0);
        if (errorCount > 0) {
            addItem('Errors', errorCount);
        }

        // Warning count
        const warningCount = ninaData.warning_events ? ninaData.warning_events.length : (ninaData.warnings ? ninaData.warnings.length : 0);
        if (warningCount > 0) {
            addItem('Warnings', warningCount);
        }

        // Hide if no items
        if (container.children.length === 0) {
            container.style.display = 'none';
        }
    }

    /**
     * Setup expand/collapse all handlers
     */
    function setupNinaExpandCollapse() {
        const expandAll = document.querySelector('.log-nina-expand-all');
        const collapseAll = document.querySelector('.log-nina-collapse-all');

        if (expandAll) {
            expandAll.addEventListener('click', () => {
                document.querySelectorAll('.log-nina-phase').forEach(phase => {
                    phase.classList.add('expanded');
                    const body = phase.querySelector('.log-nina-phase-body');
                    if (body) body.style.display = 'block';
                });
            });
        }

        if (collapseAll) {
            collapseAll.addEventListener('click', () => {
                document.querySelectorAll('.log-nina-phase').forEach(phase => {
                    phase.classList.remove('expanded');
                    const body = phase.querySelector('.log-nina-phase-body');
                    if (body) body.style.display = 'none';
                });
            });
        }
    }

    /**
     * Handle sub-tab switching
     */
    document.addEventListener('click', function(e) {
        const btn = e.target.closest('.log-analysis-tab-btn');
        if (!btn) return;

        const tabName = btn.dataset.logTab;
        const container = btn.closest('.log-analysis-container');
        if (!container) return;

        // Update button states
        container.querySelectorAll('.log-analysis-tab-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');

        // Update content visibility
        container.querySelectorAll('.log-analysis-content').forEach(c => c.classList.remove('active'));
        const targetContent = container.querySelector(`#log-${tabName}-tab`);
        if (targetContent) targetContent.classList.add('active');

        // Resize charts in the newly visible tab (fixes compressed charts after theme change)
        requestAnimationFrame(function() {
            resizeChartsForTab(tabName);
        });
    });

    /**
     * Resize charts for a specific tab
     */
    function resizeChartsForTab(tabName) {
        const resizeChart = function(chart) {
            if (chart && typeof chart.resize === 'function') {
                chart.resize();
                chart.update('none');
            }
        };

        switch(tabName) {
            case 'overview':
                // Overview has SVG swimlane, re-render if needed
                if (logData && logData.asiair) {
                    const sessionStartStr = logData.asiair.session_start ||
                        (logData.phd2 && logData.phd2.session_start);
                    const sessionStart = sessionStartStr ? new Date(sessionStartStr) : null;
                    const hoursToTime = function(hours) {
                        if (!sessionStart) return hours.toFixed(1) + 'h';
                        const date = new Date(sessionStart.getTime() + hours * 3600 * 1000);
                        return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
                    };
                    renderOverviewSwimlane(logData.asiair, sessionStart, hoursToTime);
                }
                resizeChart(charts.autocenter);
                break;
            case 'guiding':
                resizeChart(charts.guiding);
                resizeChart(charts.guidePulseScatter);
                resizeChart(charts.guidePulseDuration);
                resizeChart(charts.guidingSnr);
                break;
            case 'dithering':
                resizeChart(charts.dither);
                break;
            case 'autofocus':
                resizeChart(charts.afOverlay);
                resizeChart(charts.afDrift);
                charts.afCurves.forEach(resizeChart);
                break;
            case 'nina':
                // NINA tab has no charts, just re-render timeline if needed
                if (logData && logData.nina && logData.nina.timeline_phases) {
                    renderNinaTimeline(logData.nina);
                }
                break;
        }
    }

    /**
     * Cleanup function for when leaving session view
     */
    window.cleanupSessionLogCharts = function() {
        if (charts.guiding) { charts.guiding.destroy(); charts.guiding = null; }
        if (charts.dither) { charts.dither.destroy(); charts.dither = null; }
        if (charts.guidePulseScatter) { charts.guidePulseScatter.destroy(); charts.guidePulseScatter = null; }
        if (charts.guidePulseDuration) { charts.guidePulseDuration.destroy(); charts.guidePulseDuration = null; }
        if (charts.guidingSnr) { charts.guidingSnr.destroy(); charts.guidingSnr = null; }
        if (charts.autocenter) { charts.autocenter.destroy(); charts.autocenter = null; }
        if (charts.afOverlay) { charts.afOverlay.destroy(); charts.afOverlay = null; }
        if (charts.afDrift) { charts.afDrift.destroy(); charts.afDrift = null; }
        charts.afCurves.forEach(c => c.destroy());
        charts.afCurves = [];
        logData = null;
    };

    /**
     * Update all charts when theme changes.
     * Updates chart colors AND resizes to prevent Y-axis stretch.
     * Uses delay + requestAnimationFrame to ensure CSS transitions complete.
     */
    let themeResizeTimeout = null;
    window.addEventListener('themeChanged', function() {
        if (!logData || !logData.has_logs) return;

        // Debounce: only trigger once per theme change
        if (themeResizeTimeout) {
            clearTimeout(themeResizeTimeout);
        }

        // Wait for CSS transition (300ms) then update charts
        themeResizeTimeout = setTimeout(function() {
            requestAnimationFrame(function() {
                // Update and resize all Chart.js instances
                Object.keys(charts).forEach(function(key) {
                    if (key === 'afCurves') {
                        // Array of chart instances
                        charts.afCurves.forEach(function(chart) {
                            if (chart && typeof chart.resize === 'function') {
                                updateChartThemeColors(chart);
                                chart.resize();
                                chart.update('none');
                            }
                        });
                    } else if (charts[key] && typeof charts[key].resize === 'function') {
                        updateChartThemeColors(charts[key]);
                        charts[key].resize();
                        charts[key].update('none');
                    }
                });

                // Re-render SVG swimlane (needs full redraw for color changes)
                if (logData.asiair) {
                    const sessionStartStr = logData.asiair.session_start ||
                        (logData.phd2 && logData.phd2.session_start);
                    const sessionStart = sessionStartStr ? new Date(sessionStartStr) : null;
                    const hoursToTime = function(hours) {
                        if (!sessionStart) return hours.toFixed(1) + 'h';
                        const date = new Date(sessionStart.getTime() + hours * 3600 * 1000);
                        return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
                    };
                    renderOverviewSwimlane(logData.asiair, sessionStart, hoursToTime);
                }

                themeResizeTimeout = null;
            });
        }, 300);
    });

})();
