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

    // --- Color Scheme (Dark theme optimized) ---
    const COLORS = {
        ra: '#60a5fa',        // Blue for RA
        dec: '#f472b6',       // Pink for Dec
        total: '#a78bfa',     // Purple for Total
        success: '#34d399',   // Green for success
        timeout: '#ef4444',   // Red for timeout
        exposures: '#3b82f6', // Blue for exposures
        dither: '#f472b6',    // Pink for dither
        af: '#fbbf24',        // Yellow for autofocus
        meridianFlip: '#10b981', // Emerald green for meridian flip
        grid: 'rgba(150, 150, 150, 0.3)',
        text: '#b0b0b0',
        background: '#0a0f1e'
    };

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
    const AF_DRIFT_COLOR = '#f59e0b';  // Amber/Gold for drift line
    const AF_TEMP_DRIFT_COLOR = '#f59e0b';  // Amber/Gold for temp drift card

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
                    <p style="font-size: 0.9em;">Edit the session to upload ASIAIR or PHD2 logs.</p>
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
     */
    function isDarkTheme() {
        return window.stylingUtils && window.stylingUtils.isDarkTheme
            ? window.stylingUtils.isDarkTheme()
            : true;
    }

    /**
     * Get common Chart.js options
     */
    function getChartOptions(title, yLabel, xLabel = 'Hours') {
        const dark = isDarkTheme();
        return {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,
            plugins: {
                title: {
                    display: !!title,
                    text: title,
                    color: dark ? COLORS.text : '#333'
                },
                legend: {
                    labels: {
                        color: dark ? COLORS.text : '#333',
                        usePointStyle: true,
                        pointStyle: 'circle'
                    }
                },
                tooltip: {
                    backgroundColor: dark ? 'rgba(40, 40, 40, 0.92)' : 'rgba(255, 255, 255, 0.92)',
                    titleColor: dark ? '#e0e0e0' : '#222',
                    bodyColor: dark ? '#ccc' : '#444',
                    borderColor: dark ? 'rgba(100, 100, 100, 0.5)' : 'rgba(0, 0, 0, 0.15)',
                    borderWidth: 1
                }
            },
            scales: {
                x: {
                    title: {
                        display: true,
                        text: xLabel,
                        color: dark ? COLORS.text : '#333'
                    },
                    ticks: {
                        color: dark ? COLORS.text : '#666',
                        maxTicksLimit: 10
                    },
                    grid: {
                        color: dark ? COLORS.grid : 'rgba(0, 0, 0, 0.1)'
                    }
                },
                y: {
                    title: {
                        display: true,
                        text: yLabel,
                        color: dark ? COLORS.text : '#333'
                    },
                    ticks: {
                        color: dark ? COLORS.text : '#666'
                    },
                    grid: {
                        color: dark ? COLORS.grid : 'rgba(0, 0, 0, 0.1)'
                    }
                }
            }
        };
    }

    /**
     * Render Overview Tab - Key stats and swimlane timeline
     */
    function renderOverviewTab() {
        const asiair = logData.asiair;
        const phd2 = logData.phd2;

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

        // Get session start time for clock display (prefer ASIAIR, fall back to PHD2)
        const sessionStartStr = (asiair && asiair.session_start) ? asiair.session_start
                            : (phd2 && phd2.session_start) ? phd2.session_start
                            : null;
        const sessionStart = sessionStartStr ? new Date(sessionStartStr) : null;

        // Helper to convert hours to clock time string
        const hoursToTime = (hours) => {
            if (!sessionStart) return hours.toFixed(1) + 'h';
            const date = new Date(sessionStart.getTime() + hours * 3600 * 1000);
            return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
        };

        // Render swimlane timeline
        renderOverviewSwimlane(asiair, sessionStart, hoursToTime);

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
            { id: 'exposures', label: 'Exposures', color: '#3b82f6', events: exposures },
            { id: 'dithers', label: 'Dithers', color: '#ef4444', events: dithers },
            { id: 'af', label: 'AutoFocus', color: '#fbbf24', events: afRuns },
            { id: 'mf', label: 'Meridian Flip', color: '#10b981', events: meridianFlips }
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
        const textColor = dark ? '#b0b0b0' : '#666';
        const labelColor = dark ? '#e0e0e0' : '#333';
        const dividerColor = dark ? 'rgba(150, 150, 150, 0.3)' : 'rgba(0, 0, 0, 0.1)';

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
                    const color = event.ok ? row.color : '#ff6384';
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
            console.log('Guiding chart: session_start =', sessionStartStr, '→', sessionStart.toLocaleString());
        } else {
            console.log('Guiding chart: No session_start available, using hours offset');
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
                                color: dark ? 'rgba(176, 176, 176, 0.7)' : 'rgba(100, 100, 100, 0.7)',
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
                    ctx.strokeStyle = '#f97316';  // Orange/red
                    ctx.lineWidth = 1.5;
                    ctx.setLineDash([6, 4]);
                    ctx.beginPath();
                    ctx.moveTo(chart.chartArea.left, y);
                    ctx.lineTo(chart.chartArea.right, y);
                    ctx.stroke();

                    // Label at right end
                    ctx.fillStyle = '#f97316';
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
        const asiair = logData.asiair;
        if (!asiair || !asiair.af_runs || asiair.af_runs.length === 0) {
            const tab = document.getElementById('log-autofocus-tab');
            if (tab) {
                tab.innerHTML = '<p style="color: var(--text-muted); text-align: center; padding: 40px;">No autofocus data available.</p>';
            }
            return;
        }

        const afRuns = asiair.af_runs;
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

        // --- Individual V-Curve Cards (existing functionality) ---
        const container = document.getElementById('log-af-runs');
        if (!container) return;
        container.innerHTML = '';

        afRuns.forEach((afRun, index) => {
            const card = document.createElement('div');
            card.className = 'log-af-vcurve-card';

            const title = document.createElement('h5');
            const timeStr = afRun.ts ? new Date(afRun.ts).toLocaleTimeString() : `Run ${afRun.run}`;
            title.textContent = `AF Run ${afRun.run} - ${timeStr}`;
            const focusPos = getSettledPosition(afRun);
            if (focusPos !== null) {
                title.textContent += ` (Focus: ${focusPos})`;
            }
            if (afRun.temp !== null && afRun.temp !== undefined) {
                title.textContent += ` @ ${afRun.temp}°C`;
            }
            title.style.borderLeft = `4px solid ${getRunColor(index)}`;
            title.style.paddingLeft = '8px';
            card.appendChild(title);

            if (afRun.points && afRun.points.length > 0) {
                const canvas = document.createElement('canvas');
                canvas.id = `af-curve-${index}`;
                card.appendChild(canvas);

                const chart = new Chart(canvas, {
                    type: 'line',
                    data: {
                        labels: afRun.points.map(p => p.pos),
                        datasets: [{
                            label: 'Star Size (HFR)',
                            data: afRun.points.map(p => p.sz),
                            borderColor: COLORS.ra,
                            backgroundColor: 'rgba(96, 165, 250, 0.1)',
                            borderWidth: 2,
                            pointRadius: 4,
                            tension: 0.3,
                            fill: true
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        animation: false,
                        plugins: {
                            legend: { display: false }
                        },
                        scales: {
                            x: {
                                title: { display: true, text: 'Focus Position', color: dark ? COLORS.text : '#333' },
                                ticks: { color: dark ? COLORS.text : '#666', maxTicksLimit: 8 },
                                grid: { color: dark ? COLORS.grid : 'rgba(0, 0, 0, 0.1)' }
                            },
                            y: {
                                title: { display: true, text: 'Star Size', color: dark ? COLORS.text : '#333' },
                                ticks: { color: dark ? COLORS.text : '#666' },
                                grid: { color: dark ? COLORS.grid : 'rgba(0, 0, 0, 0.1)' }
                            }
                        }
                    }
                });
                charts.afCurves.push(chart);
            } else {
                card.innerHTML += '<p style="color: var(--text-muted); font-size: 0.9em;">No V-curve points recorded.</p>';
            }

            container.appendChild(card);
        });
    }

    /**
     * Task 2: Render AF Summary Cards Row
     */
    function renderAFSummaryCards(afRuns, getRunColor, getSettledPosition) {
        const container = document.getElementById('log-af-summary-cards');
        if (!container) return;
        container.innerHTML = '';

        // One card per AF run
        afRuns.forEach((afRun, idx) => {
            const color = getRunColor(idx);
            const focusPos = getSettledPosition(afRun);
            const card = document.createElement('div');
            card.className = 'log-af-stat-card';

            let subtitle = '';
            if (afRun.temp !== null && afRun.temp !== undefined && focusPos !== null) {
                subtitle = `${afRun.temp}°C → pos ${focusPos}`;
            } else if (focusPos !== null) {
                subtitle = `pos ${focusPos}`;
            } else if (afRun.temp !== null && afRun.temp !== undefined) {
                subtitle = `${afRun.temp}°C`;
            }

            card.innerHTML = `
                <div class="stat-value" style="color: ${color};">${focusPos !== null ? focusPos : '—'}</div>
                <div class="stat-label">AF Run ${afRun.run}</div>
                ${subtitle ? `<div class="stat-subtitle">${subtitle}</div>` : ''}
            `;
            container.appendChild(card);
        });

        // Temperature Drift Card
        const temps = afRuns.filter(r => r.temp !== null && r.temp !== undefined).map(r => r.temp);
        if (temps.length >= 2) {
            const tempDrift = temps[temps.length - 1] - temps[0];
            const tempCard = document.createElement('div');
            tempCard.className = 'log-af-stat-card';
            tempCard.innerHTML = `
                <div class="stat-value" style="color: ${AF_TEMP_DRIFT_COLOR};">${tempDrift >= 0 ? '+' : ''}${tempDrift.toFixed(1)}°C</div>
                <div class="stat-label">Temp Drift</div>
                <div class="stat-subtitle">${temps[0]}°C → ${temps[temps.length - 1]}°C</div>
            `;
            container.appendChild(tempCard);
        }

        // Focus Shift Card
        const positions = afRuns.map(r => getSettledPosition(r)).filter(p => p !== null);
        if (positions.length >= 2) {
            const focusShift = positions[positions.length - 1] - positions[0];
            const shiftCard = document.createElement('div');
            shiftCard.className = 'log-af-stat-card';
            shiftCard.innerHTML = `
                <div class="stat-value" style="color: ${AF_DRIFT_COLOR};">${focusShift >= 0 ? '+' : ''}${focusShift}</div>
                <div class="stat-label">Focus Shift</div>
                <div class="stat-subtitle">EAF steps Run 1→${afRuns.length}</div>
            `;
            container.appendChild(shiftCard);
        }
    }

    /**
     * Task 3: Render Overlay V-Curve Chart with vertical focus lines
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

        // Build datasets
        const datasets = [];
        const focusLines = [];
        afRuns.forEach((afRun, idx) => {
            const color = getRunColor(idx);
            const tempStr = (afRun.temp !== null && afRun.temp !== undefined) ? `${afRun.temp}°C` : '';
            const label = tempStr ? `AF Run ${afRun.run} (${tempStr})` : `AF Run ${afRun.run}`;

            if (afRun.points && afRun.points.length > 0) {
                // Sort points by position for proper line rendering
                const sortedPoints = [...afRun.points].sort((a, b) => a.pos - b.pos);
                datasets.push({
                    label: label,
                    data: sortedPoints.map(p => ({ x: p.pos, y: p.sz })),
                    borderColor: color,
                    backgroundColor: color + '33',
                    borderWidth: 2,
                    pointRadius: 6,
                    pointHoverRadius: 8,
                    showLine: true,
                    tension: 0.3,
                    fill: false
                });

                // Collect focus line data
                const focusPos = getSettledPosition(afRun);
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
                plugins: {
                    legend: {
                        display: true,
                        position: 'top',
                        labels: { color: dark ? COLORS.text : '#333', usePointStyle: true, pointStyle: 'circle' }
                    },
                    tooltip: {
                        callbacks: {
                            label: function(ctx) {
                                return `${ctx.dataset.label}: pos ${ctx.parsed.x}, size ${ctx.parsed.y.toFixed(2)}`;
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
                    const yAxis = chart.scales.y;
                    focusLines.forEach(fl => {
                        const x = xAxis.getPixelForValue(fl.pos);
                        if (x >= chart.chartArea.left && x <= chart.chartArea.right) {
                            ctx.save();
                            ctx.strokeStyle = fl.color;
                            ctx.lineWidth = 2;
                            ctx.setLineDash([6, 4]);
                            ctx.beginPath();
                            ctx.moveTo(x, chart.chartArea.top);
                            ctx.lineTo(x, chart.chartArea.bottom);
                            ctx.stroke();
                            // Label above the line
                            ctx.fillStyle = fl.color;
                            ctx.font = '11px system-ui, -apple-system, sans-serif';
                            ctx.textAlign = 'center';
                            ctx.fillText(`R${fl.run} focus`, x, chart.chartArea.top - 5);
                            ctx.restore();
                        }
                    });
                }
            }]
        });
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

        const labels = afRuns.map(r => `Run ${r.run}`);
        const data = afRuns.map(r => getSettledPosition(r));
        const minPos = Math.min(...positions);
        const maxPos = Math.max(...positions);
        const padding = 50;

        charts.afDrift = new Chart(canvas, {
            type: 'line',
            data: {
                labels: labels,
                datasets: [{
                    label: 'Focus Position',
                    data: data,
                    borderColor: AF_DRIFT_COLOR,
                    backgroundColor: AF_DRIFT_COLOR + '33',
                    borderWidth: 3,
                    pointRadius: 8,
                    pointHoverRadius: 10,
                    pointBackgroundColor: AF_DRIFT_COLOR,
                    tension: 0.2,
                    fill: false
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
                            label: function(ctx) {
                                const val = ctx.parsed.y;
                                return val !== null ? `EAF: ${val}` : 'No position data';
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
    });

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

})();
