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
        guidePulseScatter: null,
        guidePulseDuration: null,
        autocenter: null
    };

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
            document.getElementById('log-guiding-rms').textContent =
                phd2.stats?.total_rms_as ? `${phd2.stats.total_rms_as}"` : '-';
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

        // Update stats
        document.getElementById('log-ra-rms').textContent = phd2.stats?.ra_rms_as || '-';
        document.getElementById('log-dec-rms').textContent = phd2.stats?.dec_rms_as || '-';
        document.getElementById('log-total-rms').textContent = phd2.stats?.total_rms_as || '-';
        document.getElementById('log-frame-count').textContent = phd2.stats?.total_frames?.toLocaleString() || '-';

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

        // RMS Chart
        const rmsCanvas = document.getElementById('log-guiding-chart');
        if (rmsCanvas && phd2.rms && phd2.rms.length > 0) {
            if (charts.guiding) charts.guiding.destroy();

            const dark = isDarkTheme();
            // Use actual last data point for max (no padding beyond data)
            const maxHours = phd2.rms[phd2.rms.length - 1][0];

            charts.guiding = new Chart(rmsCanvas, {
                type: 'line',
                data: {
                    datasets: [
                        {
                            label: 'RA RMS (")',
                            data: phd2.rms.map(r => ({ x: r[0], y: r[1] })),
                            borderColor: COLORS.ra,
                            backgroundColor: 'rgba(96, 165, 250, 0.1)',
                            borderWidth: 2,
                            pointRadius: 0,
                            tension: 0.2,
                            fill: true
                        },
                        {
                            label: 'Dec RMS (")',
                            data: phd2.rms.map(r => ({ x: r[0], y: r[2] })),
                            borderColor: COLORS.dec,
                            backgroundColor: 'rgba(244, 114, 182, 0.1)',
                            borderWidth: 2,
                            pointRadius: 0,
                            tension: 0.2,
                            fill: true
                        },
                        {
                            label: 'Total RMS (")',
                            data: phd2.rms.map(r => ({ x: r[0], y: r[3] })),
                            borderColor: COLORS.total,
                            backgroundColor: 'transparent',
                            borderWidth: 2,
                            borderDash: [5, 5],
                            pointRadius: 0,
                            tension: 0.2
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
                            max: 15,  // Cap at 15" - values above are outliers that compress the useful range
                            title: { display: true, text: 'RMS (arcsec)', color: dark ? COLORS.text : '#333' },
                            ticks: { color: dark ? COLORS.text : '#666' },
                            grid: { color: dark ? COLORS.grid : 'rgba(0, 0, 0, 0.1)' }
                        }
                    }
                }
            });
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
     * Render AutoFocus Tab - V-curves for each AF run
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

        document.getElementById('log-af-total').textContent = asiair.af_runs.length;

        const container = document.getElementById('log-af-runs');
        if (!container) return;

        container.innerHTML = '';

        // Clean up old charts
        charts.afCurves.forEach(c => c.destroy());
        charts.afCurves = [];

        const dark = isDarkTheme();

        asiair.af_runs.forEach((afRun, index) => {
            const card = document.createElement('div');
            card.className = 'log-af-vcurve-card';

            const title = document.createElement('h5');
            const timeStr = afRun.ts ? new Date(afRun.ts).toLocaleTimeString() : `Run ${afRun.run}`;
            title.textContent = `AF Run ${afRun.run} - ${timeStr}`;
            if (afRun.focus_pos) {
                title.textContent += ` (Focus: ${afRun.focus_pos})`;
            }
            if (afRun.temp !== null && afRun.temp !== undefined) {
                title.textContent += ` @ ${afRun.temp}°C`;
            }
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
        if (charts.autocenter) { charts.autocenter.destroy(); charts.autocenter = null; }
        charts.afCurves.forEach(c => c.destroy());
        charts.afCurves = [];
        logData = null;
    };

})();
