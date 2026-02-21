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
        overview: null,
        guiding: null,
        correction: null,
        dither: null,
        afCurves: []
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
     * Render Overview Tab - Key stats and combined timeline
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

        // Create timeline chart combining RMS and exposures
        const canvas = document.getElementById('log-overview-chart');
        if (!canvas) return;

        if (charts.overview) charts.overview.destroy();

        const datasets = [];

        // Add RMS data from PHD2
        if (phd2 && phd2.rms && phd2.rms.length > 0) {
            datasets.push({
                label: 'RA RMS (")',
                data: phd2.rms.map(r => ({ x: r[0], y: r[1] })),
                borderColor: COLORS.ra,
                backgroundColor: 'transparent',
                borderWidth: 2,
                pointRadius: 0,
                tension: 0.2,
                yAxisID: 'y'
            });
            datasets.push({
                label: 'Dec RMS (")',
                data: phd2.rms.map(r => ({ x: r[0], y: r[2] })),
                borderColor: COLORS.dec,
                backgroundColor: 'transparent',
                borderWidth: 2,
                pointRadius: 0,
                tension: 0.2,
                yAxisID: 'y'
            });
        }

        // Add exposure markers from ASIAIR
        if (asiair && asiair.exposures && asiair.exposures.length > 0) {
            datasets.push({
                label: 'Exposures',
                data: asiair.exposures.map(e => ({ x: e.h, y: 0.05 })),
                borderColor: COLORS.exposures,
                backgroundColor: COLORS.exposures,
                showLine: false,
                pointRadius: 2,
                pointStyle: 'rectRot',
                yAxisID: 'y1'
            });
        }

        // Add dither markers
        if (asiair && asiair.dithers && asiair.dithers.length > 0) {
            datasets.push({
                label: 'Dithers',
                data: asiair.dithers.map(d => ({ x: d.h, y: 0.08 })),
                borderColor: d => d.ok ? COLORS.success : COLORS.timeout,
                backgroundColor: d => d.ok ? COLORS.success : COLORS.timeout,
                showLine: false,
                pointRadius: 4,
                pointStyle: 'triangle',
                yAxisID: 'y1'
            });
        }

        // Add AF markers
        if (asiair && asiair.af_runs && asiair.af_runs.length > 0) {
            datasets.push({
                label: 'AutoFocus',
                data: asiair.af_runs.filter(r => r.h).map(r => ({ x: r.h, y: 0.1 })),
                borderColor: COLORS.af,
                backgroundColor: COLORS.af,
                showLine: false,
                pointRadius: 6,
                pointStyle: 'star',
                yAxisID: 'y1'
            });
        }

        // Add Meridian Flip markers (green cross/plus symbol)
        if (asiair && asiair.meridian_flips && asiair.meridian_flips.length > 0) {
            datasets.push({
                label: 'Meridian Flip',
                data: asiair.meridian_flips.filter(mf => mf.h).map(mf => ({ x: mf.h, y: 0.12 })),
                borderColor: COLORS.meridianFlip,
                backgroundColor: COLORS.meridianFlip,
                showLine: false,
                pointRadius: 8,
                pointStyle: 'crossRot',  // X/cross symbol
                yAxisID: 'y1'
            });
        }

        if (datasets.length === 0) {
            canvas.parentElement.innerHTML = '<p style="color: var(--text-muted); text-align: center; padding: 20px;">No data to display.</p>';
            return;
        }

        // Calculate max hours from all datasets to end exactly at last data point
        let maxHours = 0;
        datasets.forEach(ds => {
            if (ds.data && ds.data.length > 0) {
                const dsMax = Math.max(...ds.data.map(p => p.x));
                if (dsMax > maxHours) maxHours = dsMax;
            }
        });

        // Get session start time for clock display (prefer ASIAIR, fall back to PHD2)
        // Use explicit checks since || can fail with empty strings
        const sessionStartStr = (asiair && asiair.session_start) ? asiair.session_start
                            : (phd2 && phd2.session_start) ? phd2.session_start
                            : null;
        const sessionStart = sessionStartStr ? new Date(sessionStartStr) : null;

        // Debug: log session start for troubleshooting
        if (sessionStart) {
            console.log('Overview chart: session_start =', sessionStartStr, '→', sessionStart.toLocaleString());
        } else {
            console.log('Overview chart: No session_start available, using hours offset');
        }

        // Helper to convert hours to clock time string
        const hoursToTime = (hours) => {
            if (!sessionStart) return hours.toFixed(1) + 'h';
            const date = new Date(sessionStart.getTime() + hours * 3600 * 1000);
            return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
        };

        const dark = isDarkTheme();

        charts.overview = new Chart(canvas, {
            type: 'scatter',
            data: { datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: false,
                plugins: {
                    legend: {
                        labels: { color: dark ? COLORS.text : '#333' }
                    },
                    tooltip: {
                        mode: 'nearest',
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
                        max: maxHours > 0 ? maxHours : undefined,  // End exactly at last data point
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
                        type: 'linear',
                        position: 'left',
                        title: { display: true, text: 'RMS (arcsec)', color: dark ? COLORS.text : '#333' },
                        ticks: { color: dark ? COLORS.text : '#666' },
                        grid: { color: dark ? COLORS.grid : 'rgba(0, 0, 0, 0.1)' },
                        min: 0,
                        suggestedMax: 1
                    },
                    y1: {
                        type: 'linear',
                        position: 'right',
                        display: false,
                        min: 0,
                        max: 0.15
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
                            title: { display: true, text: 'RMS (arcsec)', color: dark ? COLORS.text : '#333' },
                            ticks: { color: dark ? COLORS.text : '#666' },
                            grid: { color: dark ? COLORS.grid : 'rgba(0, 0, 0, 0.1)' }
                        }
                    }
                }
            });
        }

        // Correction scatter chart
        const corrCanvas = document.getElementById('log-correction-chart');
        if (corrCanvas && phd2.frames && phd2.frames.length > 0) {
            if (charts.correction) charts.correction.destroy();

            const dark = isDarkTheme();

            // Downsample for performance
            const step = Math.max(1, Math.floor(phd2.frames.length / 500));
            const sampledFrames = phd2.frames.filter((_, i) => i % step === 0);

            charts.correction = new Chart(corrCanvas, {
                type: 'scatter',
                data: {
                    datasets: [{
                        label: 'Guide Corrections',
                        data: sampledFrames.map(f => ({ x: f[1], y: f[2] })),
                        backgroundColor: 'rgba(96, 165, 250, 0.3)',
                        borderColor: COLORS.ra,
                        pointRadius: 1.5
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: false,
                    plugins: {
                        title: {
                            display: true,
                            text: 'Guide Correction Scatter (RA vs Dec)',
                            color: dark ? COLORS.text : '#333'
                        },
                        legend: { display: false }
                    },
                    scales: {
                        x: {
                            title: { display: true, text: 'RA Correction (px)', color: dark ? COLORS.text : '#333' },
                            ticks: { color: dark ? COLORS.text : '#666' },
                            grid: { color: dark ? COLORS.grid : 'rgba(0, 0, 0, 0.1)' }
                        },
                        y: {
                            title: { display: true, text: 'Dec Correction (px)', color: dark ? COLORS.text : '#333' },
                            ticks: { color: dark ? COLORS.text : '#666' },
                            grid: { color: dark ? COLORS.grid : 'rgba(0, 0, 0, 0.1)' }
                        }
                    }
                }
            });
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
        if (charts.overview) { charts.overview.destroy(); charts.overview = null; }
        if (charts.guiding) { charts.guiding.destroy(); charts.guiding = null; }
        if (charts.correction) { charts.correction.destroy(); charts.correction = null; }
        if (charts.dither) { charts.dither.destroy(); charts.dither = null; }
        charts.afCurves.forEach(c => c.destroy());
        charts.afCurves = [];
        logData = null;
    };

})();
