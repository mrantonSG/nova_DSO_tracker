/**
 * weather_charts.js - Chart.js visualizations for weather/imaging conditions
 *
 * Provides time-series charts for hourly weather data with:
 * - Cloud cover, seeing, transparency, wind speed datasets
 * - Imaging quality score calculation
 * - Recommended imaging window annotations
 * - Full dark/light theme support
 */
(function() {
    'use strict';

    // Guard against double initialization
    if (window.weatherChartsInitialized) return;
    window.weatherChartsInitialized = true;

    // --- Color Scheme (Brand palette - matches base.css) ---
    // NOTE: COLORS are for data elements (lines, bars). Text/grid colors use getWeatherChartThemeColors().
    const COLORS = {
        cloud: '#7eb3cc',        // Soft blue-gray for cloud cover
        seeing: '#9b8ec4',       // Muted purple for seeing
        transparency: '#5eb570', // Soft green for transparency
        wind: '#e09090',         // Soft coral for wind speed
        imagingGood: '#5eb570',  // Solid green for annotation (alpha added inline)
        imagingMarginal: '#ffc107', // Amber for marginal
        nightShade: 'rgba(42, 106, 128, 0.12)',    // Night hours background
        nowMarker: '#ffc107',    // Brand warning amber for "Now" line
    };

    // --- Chart instances for cleanup ---
    let chartInstances = {
        hourly: null
    };

    /**
     * Get theme-aware colors for chart UI elements (text, grid, tooltips).
     * Uses stylingUtils CSS variables with fallbacks for WCAG-compliant contrast.
     * @returns {Object} Theme colors: { text, textMuted, grid, tooltipBg, tooltipTitle, tooltipBody, tooltipBorder }
     */
    function getWeatherChartThemeColors() {
        const dark = window.stylingUtils?.isDarkTheme?.() ?? 
            (document.documentElement.getAttribute('data-theme') !== 'light');

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

    function calculateImagingScore(entry) {
        const cloudcover = entry.cloudcover ?? 9;
        const cloudScore = Math.max(0, 100 - ((cloudcover - 1) / 8) * 100);

        const moonIllum = entry.moon_illumination ?? 50;
        const moonScore = 100 - moonIllum;

        let seeingVal = entry.seeing;
        let seeingScore = 50;
        if (seeingVal != null && seeingVal !== -9999) {
            seeingVal = Math.abs(seeingVal);
            seeingScore = Math.max(0, 100 - ((seeingVal - 1) / 7) * 100);
        }

        const windSpeed = entry.wind_speed ?? 10;
        let windScore = 100;
        if (windSpeed <= 5) windScore = 100;
        else if (windSpeed <= 10) windScore = 85;
        else if (windSpeed <= 20) windScore = 60;
        else if (windSpeed <= 30) windScore = 30;
        else windScore = 0;

        const humidity = entry.rh2m ?? 60;
        let humidityScore = 100;
        if (humidity <= 40) humidityScore = 100;
        else if (humidity <= 60) humidityScore = 80;
        else if (humidity <= 80) humidityScore = 50;
        else humidityScore = 20;

        let transVal = entry.transparency;
        let transScore = 50;
        if (transVal != null && transVal !== -9999) {
            transVal = Math.abs(transVal);
            transScore = Math.max(0, 100 - ((transVal - 1) / 7) * 100);
        }

        const score = (cloudScore * 0.35) + (moonScore * 0.20) + (seeingScore * 0.15) +
                      (windScore * 0.15) + (humidityScore * 0.10) + (transScore * 0.05);

        return Math.round(Math.max(0, Math.min(100, score)));
    }

    function findImagingWindows(hourlyData, threshold = 70, initTime = null, minDuration = 3) {
        if (!hourlyData || hourlyData.length === 0) return [];

        const baseTime = initTime || new Date();
        const windows = [];
        let currentWindow = null;
        const checkNight = typeof window.isNightHour === 'function' ? window.isNightHour : function(h) { return h >= 18 || h < 6; };

        for (let i = 0; i < hourlyData.length; i++) {
            const entry = hourlyData[i];
            const time = new Date(baseTime.getTime() + entry.timepoint * 3600000);
            const hour = time.getHours();
            const isNight = checkNight(hour);
            
            if (!isNight) {
                if (currentWindow) {
                    currentWindow.avgScore = Math.round(
                        currentWindow.scores.reduce((a, b) => a + b, 0) / currentWindow.scores.length
                    );
                    currentWindow.duration = currentWindow.scores.length;
                    delete currentWindow.scores;
                    if (currentWindow.duration >= minDuration) {
                        windows.push(currentWindow);
                    }
                    currentWindow = null;
                }
                continue;
            }
            
            const score = calculateImagingScore(entry);

            if (score >= threshold) {
                if (!currentWindow) {
                    currentWindow = { start: time, end: time, scores: [score] };
                } else {
                    currentWindow.end = time;
                    currentWindow.scores.push(score);
                }
            } else if (currentWindow) {
                currentWindow.avgScore = Math.round(
                    currentWindow.scores.reduce((a, b) => a + b, 0) / currentWindow.scores.length
                );
                currentWindow.duration = currentWindow.scores.length;
                delete currentWindow.scores;
                if (currentWindow.duration >= minDuration) {
                    windows.push(currentWindow);
                }
                currentWindow = null;
            }
        }

        if (currentWindow) {
            currentWindow.avgScore = Math.round(
                currentWindow.scores.reduce((a, b) => a + b, 0) / currentWindow.scores.length
            );
            currentWindow.duration = currentWindow.scores.length;
            delete currentWindow.scores;
            if (currentWindow.duration >= minDuration) {
                windows.push(currentWindow);
            }
        }

        return windows;
    }

    function getWeatherChartOptions(themeColors) {
        return {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                mode: 'index',
                intersect: false
            },
            scales: {
                x: {
                    type: 'time',
                    time: {
                        unit: 'hour',
                        displayFormats: { hour: 'HH:mm' },
                        tooltipFormat: 'EEE HH:mm'
                    },
                    adapters: {
                        date: { zone: 'local' }
                    },
                    grid: { color: themeColors.grid },
                    ticks: { color: themeColors.textMuted, maxRotation: 0 }
                },
                quality: {
                    type: 'linear',
                    position: 'left',
                    min: 1,
                    max: 9,
                    reverse: true,
                    title: { display: true, text: 'Quality (1=Best)', color: themeColors.textMuted },
                    grid: { color: themeColors.grid },
                    ticks: { color: themeColors.textMuted, stepSize: 1 }
                },
                wind: {
                    type: 'linear',
                    position: 'right',
                    min: 0,
                    title: { display: true, text: 'Wind (m/s)', color: themeColors.textMuted },
                    grid: { display: false },
                    ticks: { color: themeColors.textMuted }
                }
            },
            plugins: {
                legend: {
                    position: 'top',
                    labels: { color: themeColors.text, usePointStyle: true, boxWidth: 8 }
                },
                tooltip: {
                    backgroundColor: themeColors.tooltipBg,
                    titleColor: themeColors.tooltipTitle,
                    bodyColor: themeColors.tooltipBody,
                    borderColor: themeColors.tooltipBorder,
                    borderWidth: 1,
                    padding: 12,
                    callbacks: {
                        afterBody: function(tooltipItems) {
                            if (!tooltipItems.length) return '';
                            const chart = tooltipItems[0].chart;
                            const idx = tooltipItems[0].dataIndex;
                            const hourlyData = chart._hourlyData;
                            if (!hourlyData || !hourlyData[idx]) return '';
                            const entry = hourlyData[idx];
                            const labelDate = tooltipItems[0].label ? new Date(tooltipItems[0].parsed.x) : null;
                            const hour = labelDate ? labelDate.getHours() : 12;
                            const checkNight = typeof window.isNightHour === 'function' ? window.isNightHour : function(h) { return h >= 18 || h < 6; };
                            const isNight = checkNight(hour);
                            
                            let scoreDisplay, conditionsDisplay;
                            if (isNight) {
                                const score = calculateImagingScore(entry);
                                const quality = getQualityLabel(score);
                                scoreDisplay = score + ' ' + quality.icon;
                                conditionsDisplay = quality.label;
                            } else {
                                scoreDisplay = 'N/A ☀️';
                                conditionsDisplay = 'Daytime';
                            }
                            
                            return [
                                '',
                                '─────────────────',
                                'Imaging Score: ' + scoreDisplay,
                                'Conditions: ' + conditionsDisplay,
                                '',
                                'Humidity: ' + (entry.rh2m ?? '?') + '%',
                                'Temp: ' + (entry.temp2m != null ? entry.temp2m.toFixed(1) + '°C' : '?'),
                                'Moon: ' + (entry.moon_illumination != null ? entry.moon_illumination.toFixed(0) + '%' : '?')
                            ];
                        }
                    }
                }
            }
        };
    }

    function createWeatherChart(canvasId, hourlyData, options = {}) {
        if (chartInstances.hourly) {
            chartInstances.hourly.destroy();
            chartInstances.hourly = null;
        }

        const canvas = document.getElementById(canvasId);
        if (!canvas) {
            console.warn('[WeatherCharts] Canvas not found:', canvasId);
            return null;
        }

        const initTime = options.initTime || new Date();
        const labels = hourlyData.map(entry => {
            const dt = new Date(initTime.getTime() + entry.timepoint * 3600000);
            return dt;
        });

        const cloudData = hourlyData.map(e => e.cloudcover);
        const seeingData = hourlyData.map(e => {
            const v = e.seeing;
            return (v === -9999 || v == null) ? null : Math.abs(v);
        });
        const transData = hourlyData.map(e => {
            const v = e.transparency;
            return (v === -9999 || v == null) ? null : Math.abs(v);
        });
        const windData = hourlyData.map(e => e.wind_speed);

        const themeColors = getWeatherChartThemeColors();
        const chartOptions = getWeatherChartOptions(themeColors);

        const windows = findImagingWindows(hourlyData, options.threshold || 70, initTime, options.minDuration || 3);
        const windowAnnotations = {};
        windows.forEach(function(win, idx) {
            windowAnnotations['window' + idx] = {
                type: 'box',
                xMin: win.start,
                xMax: new Date(win.end.getTime() + 3600000),
                backgroundColor: 'rgba(94, 181, 112, 0.25)',
                borderColor: COLORS.imagingGood,
                borderWidth: 2,
                drawTime: 'beforeDatasetsDraw',
                label: {
                    display: true,
                    content: '★ ' + win.avgScore,
                    position: { x: 'start', y: 'start' },
                    backgroundColor: COLORS.imagingGood,
                    color: '#fff',
                    font: { size: 11, weight: 'bold' },
                    padding: 4
                }
            };
        });
        chartOptions.plugins.annotation = { annotations: windowAnnotations };

        const ctx = canvas.getContext('2d');
        const chartConfig = {
            type: 'line',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'Cloud Cover',
                        data: cloudData,
                        borderColor: COLORS.cloud,
                        backgroundColor: COLORS.cloud + '33',
                        yAxisID: 'quality',
                        tension: 0.3,
                        pointRadius: 2,
                        fill: true
                    },
                    {
                        label: 'Seeing',
                        data: seeingData,
                        borderColor: COLORS.seeing,
                        backgroundColor: 'transparent',
                        yAxisID: 'quality',
                        tension: 0.3,
                        pointRadius: 2,
                        borderDash: [5, 3]
                    },
                    {
                        label: 'Transparency',
                        data: transData,
                        borderColor: COLORS.transparency,
                        backgroundColor: 'transparent',
                        yAxisID: 'quality',
                        tension: 0.3,
                        pointRadius: 2
                    },
                    {
                        label: 'Wind',
                        data: windData,
                        borderColor: COLORS.wind,
                        backgroundColor: COLORS.wind + '22',
                        yAxisID: 'wind',
                        tension: 0.3,
                        pointRadius: 2,
                        fill: true
                    }
                ]
            },
            options: chartOptions
        };
        chartInstances.hourly = new Chart(ctx, chartConfig);
        chartInstances.hourly._hourlyData = hourlyData;

        return chartInstances.hourly;
    }

    function updateWeatherChartTheme(chart) {
        if (!chart) return;

        const themeColors = getWeatherChartThemeColors();
        const opts = chart.options;

        opts.scales.x.grid.color = themeColors.grid;
        opts.scales.x.ticks.color = themeColors.textMuted;
        opts.scales.quality.grid.color = themeColors.grid;
        opts.scales.quality.ticks.color = themeColors.textMuted;
        opts.scales.quality.title.color = themeColors.textMuted;
        opts.scales.wind.ticks.color = themeColors.textMuted;
        opts.scales.wind.title.color = themeColors.textMuted;

        opts.plugins.legend.labels.color = themeColors.text;
        opts.plugins.tooltip.backgroundColor = themeColors.tooltipBg;
        opts.plugins.tooltip.titleColor = themeColors.tooltipTitle;
        opts.plugins.tooltip.bodyColor = themeColors.tooltipBody;
        opts.plugins.tooltip.borderColor = themeColors.tooltipBorder;

        chart.update('none');
    }

    function updateWeatherChartData(chart, newData, initTime = null) {
        if (!chart || !newData) return;

        const baseTime = initTime || new Date();
        const labels = newData.map(entry => {
            return new Date(baseTime.getTime() + entry.timepoint * 3600000);
        });

        chart.data.labels = labels;
        chart.data.datasets[0].data = newData.map(e => e.cloudcover);
        chart.data.datasets[1].data = newData.map(e => {
            const v = e.seeing;
            return (v === -9999 || v == null) ? null : Math.abs(v);
        });
        chart.data.datasets[2].data = newData.map(e => {
            const v = e.transparency;
            return (v === -9999 || v == null) ? null : Math.abs(v);
        });
        chart.data.datasets[3].data = newData.map(e => e.wind_speed);
        chart._hourlyData = newData;

        chart.update();
    }

    function getQualityLabel(score) {
        if (score >= 80) return { label: 'Excellent', icon: '✨', cssClass: 'excellent' };
        if (score >= 65) return { label: 'Good', icon: '👍', cssClass: 'good' };
        if (score >= 50) return { label: 'Marginal', icon: '⚠️', cssClass: 'marginal' };
        return { label: 'Poor', icon: '❌', cssClass: 'poor' };
    }

    function formatTime(date) {
        return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
    }

    function renderScoreCard(container, windows, i18n = {}) {
        if (!container) return;

        const labels = {
            bestWindow: i18n.bestWindow || 'Best Imaging Window',
            noGoodWindows: i18n.noGoodWindows || 'No optimal conditions in forecast',
            duration: i18n.duration || 'Duration',
            hours: i18n.hours || 'h',
            score: i18n.score || 'Score'
        };

        if (!windows || windows.length === 0) {
            container.innerHTML = `
                <div class="weather-score-card">
                    <div class="weather-score-card__icon poor">
                        <span>⛅</span>
                    </div>
                    <div class="weather-score-card__content">
                        <p class="weather-score-card__title">${labels.noGoodWindows}</p>
                        <p class="weather-score-card__subtitle">Check back later</p>
                    </div>
                </div>
            `;
            return;
        }

        const best = windows.reduce((a, b) => (a.avgScore >= b.avgScore ? a : b));
        const quality = getQualityLabel(best.avgScore);
        const startStr = formatTime(best.start);
        const endDisplay = new Date(best.end.getTime() + 3600000);
        const endStr = formatTime(endDisplay);

        container.innerHTML = `
            <div class="weather-score-card">
                <div class="weather-score-card__icon ${quality.cssClass}">
                    <span>🌙</span>
                </div>
                <div class="weather-score-card__content">
                    <p class="weather-score-card__title">${labels.bestWindow}</p>
                    <p class="weather-score-card__subtitle">${startStr} – ${endStr} (${best.duration}${labels.hours}) · ${quality.label}</p>
                </div>
                <div>
                    <div class="weather-score-card__score">${best.avgScore}</div>
                    <div class="weather-score-card__score-label">${labels.score}</div>
                </div>
            </div>
        `;
    }

    /**
     * Calculate imaging score from daily averages (night-specific values).
     * Uses night_cloudcover_avg, night_seeing_avg, night_transparency_avg.
     */
    function calculateDailyImagingScore(day) {
        const cloudVal = day.night_cloudcover_avg ?? day.cloudcover ?? 9;
        const cloudScore = Math.max(0, 100 - ((cloudVal - 1) / 8) * 100);

        let seeingVal = day.night_seeing_avg ?? day.seeing;
        let seeingScore = 50;
        if (seeingVal != null && seeingVal !== -9999) {
            seeingVal = Math.abs(seeingVal);
            seeingScore = Math.max(0, 100 - ((seeingVal - 1) / 7) * 100);
        }

        let transVal = day.night_transparency_avg ?? day.transparency;
        let transScore = 50;
        if (transVal != null && transVal !== -9999) {
            transVal = Math.abs(transVal);
            transScore = Math.max(0, 100 - ((transVal - 1) / 7) * 100);
        }

        const windSpeed = day.wind_avg ?? day.wind_speed ?? 10;
        let windScore = 100;
        if (windSpeed <= 5) windScore = 100;
        else if (windSpeed <= 10) windScore = 85;
        else if (windSpeed <= 20) windScore = 60;
        else if (windSpeed <= 30) windScore = 30;
        else windScore = 0;

        const humidity = day.rh2m_avg ?? day.rh2m ?? 60;
        let humidityScore = 100;
        if (humidity <= 40) humidityScore = 100;
        else if (humidity <= 60) humidityScore = 80;
        else if (humidity <= 80) humidityScore = 50;
        else humidityScore = 20;

        const score = (cloudScore * 0.40) + (seeingScore * 0.20) + 
                      (transScore * 0.15) + (windScore * 0.15) + (humidityScore * 0.10);

        return Math.round(Math.max(0, Math.min(100, score)));
    }

    /**
     * Find best imaging nights from daily data.
     */
    function findBestImagingNights(dailyData, threshold = 60, initTime = null) {
        if (!dailyData || dailyData.length === 0) return [];

        const baseTime = initTime || new Date();
        const nights = [];

        for (let i = 0; i < dailyData.length; i++) {
            const day = dailyData[i];
            const score = calculateDailyImagingScore(day);
            
            let dateStr = '';
            if (day.date) {
                dateStr = day.date;
            } else if (day.day_index !== undefined) {
                const d = new Date(baseTime.getTime() + day.day_index * 24 * 3600000);
                dateStr = d.toLocaleDateString();
            }

            if (score >= threshold) {
                nights.push({
                    date: dateStr,
                    score: score,
                    dayIndex: i,
                    cloudcover: day.night_cloudcover_avg ?? day.cloudcover,
                    seeing: day.night_seeing_avg ?? day.seeing,
                    transparency: day.night_transparency_avg ?? day.transparency
                });
            }
        }

        nights.sort((a, b) => b.score - a.score);
        return nights;
    }

    /**
     * Create a daily weather chart (7-day bar chart).
     */
    function createDailyWeatherChart(canvasId, dailyData, options = {}) {
        if (chartInstances.daily) {
            chartInstances.daily.destroy();
            chartInstances.daily = null;
        }

        const canvas = document.getElementById(canvasId);
        if (!canvas) {
            console.warn('[WeatherCharts] Daily canvas not found:', canvasId);
            return null;
        }

        const initTime = options.initTime || new Date();
        const labels = dailyData.map((day, idx) => {
            if (day.date) return day.date;
            const d = new Date(initTime.getTime() + (day.day_index ?? idx) * 24 * 3600000);
            return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
        });

        const cloudData = dailyData.map(d => d.night_cloudcover_avg ?? d.cloudcover ?? null);
        const seeingData = dailyData.map(d => {
            const v = d.night_seeing_avg ?? d.seeing;
            return (v === -9999 || v == null) ? null : Math.abs(v);
        });
        const transData = dailyData.map(d => {
            const v = d.night_transparency_avg ?? d.transparency;
            return (v === -9999 || v == null) ? null : Math.abs(v);
        });
        const scoreData = dailyData.map(d => calculateDailyImagingScore(d));

        const themeColors = getWeatherChartThemeColors();

        const chart = new Chart(canvas, {
            type: 'bar',
            data: {
                labels: labels,
                datasets: [
                    {
                        label: 'Imaging Score',
                        data: scoreData,
                        backgroundColor: scoreData.map(s => {
                            if (s >= 70) return 'rgba(94, 181, 112, 0.7)';
                            if (s >= 50) return 'rgba(255, 193, 7, 0.7)';
                            return 'rgba(224, 144, 144, 0.7)';
                        }),
                        borderColor: scoreData.map(s => {
                            if (s >= 70) return COLORS.transparency;
                            if (s >= 50) return COLORS.nowMarker;
                            return COLORS.wind;
                        }),
                        borderWidth: 1,
                        yAxisID: 'score',
                        order: 2
                    },
                    {
                        label: 'Cloud Cover',
                        data: cloudData,
                        type: 'line',
                        borderColor: COLORS.cloud,
                        backgroundColor: 'transparent',
                        borderWidth: 2,
                        pointRadius: 4,
                        yAxisID: 'quality',
                        order: 1
                    },
                    {
                        label: 'Seeing',
                        data: seeingData,
                        type: 'line',
                        borderColor: COLORS.seeing,
                        backgroundColor: 'transparent',
                        borderWidth: 2,
                        borderDash: [5, 5],
                        pointRadius: 4,
                        yAxisID: 'quality',
                        order: 1
                    }
                ]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: {
                    mode: 'index',
                    intersect: false
                },
                scales: {
                    x: {
                        grid: { color: themeColors.grid },
                        ticks: { color: themeColors.textMuted }
                    },
                    score: {
                        type: 'linear',
                        position: 'left',
                        min: 0,
                        max: 100,
                        title: { display: true, text: 'Imaging Score', color: themeColors.textMuted },
                        grid: { color: themeColors.grid },
                        ticks: { color: themeColors.textMuted }
                    },
                    quality: {
                        type: 'linear',
                        position: 'right',
                        min: 1,
                        max: 9,
                        reverse: true,
                        title: { display: true, text: 'Quality (1=Best)', color: themeColors.textMuted },
                        grid: { display: false },
                        ticks: { color: themeColors.textMuted, stepSize: 1 }
                    }
                },
                plugins: {
                    legend: {
                        position: 'top',
                        labels: { color: themeColors.text }
                    },
                    tooltip: {
                        backgroundColor: themeColors.tooltipBg,
                        titleColor: themeColors.tooltipTitle,
                        bodyColor: themeColors.tooltipBody,
                        borderColor: themeColors.tooltipBorder,
                        borderWidth: 1,
                        callbacks: {
                            afterBody: function(tooltipItems) {
                                if (!tooltipItems.length) return '';
                                const idx = tooltipItems[0].dataIndex;
                                const day = dailyData[idx];
                                const score = calculateDailyImagingScore(day);
                                const quality = getQualityLabel(score);
                                return [
                                    '',
                                    '─────────────────',
                                    'Night Quality: ' + quality.label + ' ' + quality.icon,
                                    'Cloud: ' + (day.night_cloudcover_avg ?? day.cloudcover ?? '?') + '/9',
                                    'Seeing: ' + (day.night_seeing_avg ?? day.seeing ?? '?') + '/8',
                                    'Transparency: ' + (day.night_transparency_avg ?? day.transparency ?? '?') + '/8'
                                ];
                            }
                        }
                    }
                }
            }
        });

        chart._dailyData = dailyData;
        chartInstances.daily = chart;
        return chart;
    }

    /**
     * Render daily score card showing best nights.
     */
    function renderDailyScoreCard(container, nights, i18n = {}) {
        const el = typeof container === 'string' ? document.getElementById(container) : container;
        if (!el) return;

        const labels = {
            bestNight: i18n.bestNight || 'Best Imaging Night',
            noGoodNights: i18n.noGoodNights || 'No ideal imaging nights in forecast',
            score: i18n.score || 'Score'
        };

        if (!nights || nights.length === 0) {
            el.innerHTML = `
                <div class="weather-score-card weather-score-card--empty">
                    <span class="weather-score-card__icon poor">❌</span>
                    <span class="weather-score-card__title">${labels.noGoodNights}</span>
                </div>
            `;
            return;
        }

        const best = nights[0];
        const quality = getQualityLabel(best.score);

        el.innerHTML = `
            <div class="weather-score-card">
                <div class="weather-score-card__icon ${quality.cssClass}">
                    <span>🌙</span>
                </div>
                <div class="weather-score-card__content">
                    <p class="weather-score-card__title">${labels.bestNight}</p>
                    <p class="weather-score-card__subtitle">${best.date} · ${quality.label}</p>
                </div>
                <div>
                    <div class="weather-score-card__score">${best.score}</div>
                    <div class="weather-score-card__score-label">${labels.score}</div>
                </div>
            </div>
        `;
    }

    /**
     * Initialize the weather charts module.
     * Called after DOM ready and weather data loaded.
     */
    function initWeatherCharts() {
        console.log('[WeatherCharts] Module initialized');
    }

    // --- Export to window ---
    window.initWeatherCharts = initWeatherCharts;
    window.WeatherCharts = {
        // Core functions (hourly)
        create: createWeatherChart,
        updateTheme: updateWeatherChartTheme,
        updateData: updateWeatherChartData,
        
        // Daily chart functions
        createDaily: createDailyWeatherChart,
        calculateDailyScore: calculateDailyImagingScore,
        findBestNights: findBestImagingNights,
        renderDailyScoreCard: renderDailyScoreCard,
        
        // Imaging score functions (hourly)
        calculateScore: calculateImagingScore,
        findWindows: findImagingWindows,
        renderScoreCard: renderScoreCard,
        
        // Theme helpers
        getThemeColors: getWeatherChartThemeColors,
        COLORS: COLORS,
        
        // Instance storage (for cleanup/reference)
        instances: chartInstances
    };

    let themeResizeTimeout = null;
    window.addEventListener('themeChanged', function() {
        if (themeResizeTimeout) clearTimeout(themeResizeTimeout);
        themeResizeTimeout = setTimeout(function() {
            requestAnimationFrame(function() {
                Object.keys(chartInstances).forEach(function(key) {
                    const chart = chartInstances[key];
                    if (chart && typeof chart.resize === 'function') {
                        updateWeatherChartTheme(chart);
                        chart.resize();
                        chart.update('none');
                    }
                });
                themeResizeTimeout = null;
            });
        }, 300);
    });

})();
