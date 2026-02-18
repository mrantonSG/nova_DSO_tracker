(function() {
    'use strict';

    let heatmapLoaded = false;
    let globalHeatmapData = null;
    let isFetching = false;
    let currentFilteredIds = [];
    let currentFilteredY = [];

    // Force Plotly to resize when the browser window changes
    window.addEventListener('resize', function() {
        const plotDiv = document.getElementById('yearly-heatmap-plot');
        if (heatmapLoaded && plotDiv && typeof Plotly !== 'undefined') {
            Plotly.Plots.resize(plotDiv);
        }
    });

    function updateHeatmapFilter() {
        renderHeatmapFromCache();
    }

    function fetchAndRenderHeatmap() {
        const plotDiv = document.getElementById('yearly-heatmap-plot');
        const loadingDiv = document.getElementById("heatmap-loading");
        const progressBar = document.getElementById("heatmap-progress-bar");
        const loadingText = document.getElementById("heatmap-loading-text");

        const currentLoc = sessionStorage.getItem('selectedLocation') || '';

        // 1. If in memory and location matches, just resize
        if (heatmapLoaded && globalHeatmapData && globalHeatmapData._location === currentLoc) {
            Plotly.Plots.resize(plotDiv);
            return;
        }

        // 2. Check if already fetching (e.g. tab switch)
        if (isFetching) {
            if (loadingDiv) loadingDiv.style.display = "block";
            return;
        }

        // 3. Check Browser Cache (LocalStorage)
        const cacheKey = `nova_heatmap_${currentLoc.replace(/[^a-zA-Z0-9]/g, '_')}`;
        try {
            const cachedStr = localStorage.getItem(cacheKey);
            if (cachedStr) {
                const cachedObj = JSON.parse(cachedStr);
                // 24 hour expiry
                const age = (Date.now() - cachedObj.timestamp) / 1000;
                if (age < 86400) {
                    console.log("Loaded Heatmap from Browser Storage");
                    globalHeatmapData = cachedObj.data;
                    globalHeatmapData._location = currentLoc;
                    renderHeatmapFromCache();
                    return;
                }
            }
        } catch (e) { console.warn("LocalStorage read failed", e); }

        // 4. Start Chunked Fetch
        isFetching = true;
        plotDiv.innerHTML = "";
        if (loadingDiv) loadingDiv.style.display = "block";
        if (progressBar) progressBar.style.width = "0%";

        // Initialize Stitching Structure
        let stitchedData = {
            x: [], y: [], z: [], moon_phases: [], dates: [],
            ids: [], active: [], types: [], cons: [], mags: [], sizes: [], sbs: [],
            _location: currentLoc
        };

        let totalChunks = 12;
        let completedChunks = 0;

        function fetchChunk(index) {
            if (loadingText) loadingText.textContent = `Calculating month ${index + 1} of ${totalChunks}...`;

            fetch(`/api/get_yearly_heatmap_chunk?chunk_index=${index}&location_name=${encodeURIComponent(currentLoc)}`)
                .then(res => res.json())
                .then(data => {
                    if (data.error) throw new Error(data.error);

                    // Append Columns (Time data)
                    stitchedData.x = stitchedData.x.concat(data.x);
                    stitchedData.dates = stitchedData.dates.concat(data.dates);
                    stitchedData.moon_phases = stitchedData.moon_phases.concat(data.moon_phases);

                    // Metadata (Take from last chunk, assuming consistency)
                    stitchedData.y = data.y;
                    stitchedData.ids = data.ids;
                    stitchedData.active = data.active;
                    stitchedData.types = data.types;
                    stitchedData.cons = data.cons;
                    stitchedData.mags = data.mags;
                    stitchedData.sizes = data.sizes;
                    stitchedData.sbs = data.sbs;

                    // Append Z Columns to Rows
                    if (index === 0) {
                        stitchedData.z = data.z_chunk;
                    } else {
                        for (let i = 0; i < data.z_chunk.length; i++) {
                            // Safety check for row count mismatch
                            if (stitchedData.z[i]) {
                                stitchedData.z[i] = stitchedData.z[i].concat(data.z_chunk[i]);
                            }
                        }
                    }

                    completedChunks++;
                    const percent = Math.round((completedChunks / totalChunks) * 100);
                    if (progressBar) progressBar.style.width = `${percent}%`;

                    if (completedChunks < totalChunks) {
                        fetchChunk(index + 1);
                    } else {
                        // Finished
                        globalHeatmapData = stitchedData;
                        isFetching = false;

                        // Save to Browser Cache
                        try {
                            const cachePayload = { timestamp: Date.now(), data: stitchedData };
                            localStorage.setItem(cacheKey, JSON.stringify(cachePayload));
                        } catch (e) { console.warn("LocalStorage quota exceeded", e); }

                        if (loadingDiv) loadingDiv.style.display = "none";
                        renderHeatmapFromCache();
                    }
                })
                .catch(err => {
                    console.error(err);
                    isFetching = false;
                    if (loadingDiv) loadingDiv.style.display = "none";
                    plotDiv.innerHTML = `<div style="color:red; text-align:center; padding:20px;">Error: ${err.message}</div>`;
                });
        }

        fetchChunk(0);
    }

    function renderHeatmapFromCache() {
        const data = globalHeatmapData;
        const plotDiv = document.getElementById('yearly-heatmap-plot');

        // --- UPDATED: Use Global Toggle ---
        const activeToggle = document.getElementById('global-active-toggle');
        const onlyActive = activeToggle ? activeToggle.checked : false;
        // ----------------------------------

        // Get Saved View Settings from Global (index.html)
        const mainDropdown = document.getElementById('saved-views-dropdown');
        const selectedViewName = mainDropdown ? mainDropdown.value : '';

        let viewSettings = null;
        if (selectedViewName && window.allSavedViews && window.allSavedViews[selectedViewName]) {
            viewSettings = window.allSavedViews[selectedViewName].settings;
        }

        if (!data || data.error) {
            plotDiv.innerHTML = `<div style="color:red; text-align:center; padding:20px;">${data ? data.error : 'No Data'}</div>`;
            return;
        }

        // Check match logic
        const checkViewMatch = (i) => {
            if (!viewSettings) return true;

            // Type
            const typeFilter = viewSettings['dso_filter_col_key_Type'];
            if (typeFilter) {
                const cellText = (data.types[i] || "").toLowerCase();
                const filterTerms = typeFilter.toLowerCase().split(/[\s,]+/).filter(t => t.length > 0);
                if (filterTerms.length > 0) {
                    const cellTokens = cellText.split(/[\s,]+/).filter(t => t.length > 0);
                    const match = filterTerms.some(term => cellTokens.some(token => token.includes(term)));
                    if (!match) return false;
                }
            }
            // Constellation
            const conFilter = viewSettings['dso_filter_col_key_Constellation'];
            if (conFilter) {
                if (!(data.cons[i] || "").toLowerCase().includes(conFilter.toLowerCase())) return false;
            }
            // Numeric
            const checkNumeric = (val, filter) => {
                if (!filter) return true;
                if (typeof matchesNumericFilter === 'function') return matchesNumericFilter(val, filter);
                if (filter.startsWith('>')) return val > parseFloat(filter.substring(1));
                if (filter.startsWith('<')) return val < parseFloat(filter.substring(1));
                return true;
            };
            if (!checkNumeric(data.mags[i], viewSettings['dso_filter_col_key_Magnitude'])) return false;
            if (!checkNumeric(data.sizes[i], viewSettings['dso_filter_col_key_Size'])) return false;
            if (!checkNumeric(data.sbs[i], viewSettings['dso_filter_col_key_SB'])) return false;

            return true;
        };

        let filteredY = [];
        let filteredZ = [];
        let filteredIds = [];

        for(let i=0; i < data.y.length; i++) {
            if (onlyActive && data.active[i] === 0) continue;
            if (!checkViewMatch(i)) continue;

            filteredY.push(data.y[i]);
            filteredZ.push(data.z[i]);
            filteredIds.push(data.ids[i]);
        }

        if (filteredY.length === 0) {
             plotDiv.innerHTML = `<div style="color:#666; text-align:center; padding:20px; padding-top:100px; font-size: 1.2em;">No projects found.<br><br><small>Adjust filters or Saved View.</small></div>`;
             return;
        } else {
            plotDiv.innerHTML = "";
        }

        currentFilteredIds = filteredIds;
        currentFilteredY = filteredY;

        // Moon Overlays
        const shapes = [];
        if (data.moon_phases) {
            data.moon_phases.forEach((phase, index) => {
                if (phase > 80) {
                    shapes.push({
                        type: 'rect',
                        xref: 'x',
                        yref: 'paper',
                        x0: index - 0.5,
                        x1: index + 0.5,
                        y0: 0,
                        y1: 1,
                        fillcolor: 'rgba(255, 255, 255, 0.01)',
                        line: { width: 0 },
                        layer: 'above'
                    });
                }
            });
        }

        const novaColorScale = [
            [0.0, (window.stylingUtils && window.stylingUtils.getColor) ? window.stylingUtils.getColor('--heatmap-scale-0', '#ffffff') : '#ffffff'],
            [0.1, (window.stylingUtils && window.stylingUtils.getColor) ? window.stylingUtils.getColor('--heatmap-scale-10', '#f0f4f5') : '#f0f4f5'],
            [0.3, (window.stylingUtils && window.stylingUtils.getColor) ? window.stylingUtils.getColor('--heatmap-scale-30', '#dce5eb') : '#dce5eb'],
            [0.6, (window.stylingUtils && window.stylingUtils.getColor) ? window.stylingUtils.getColor('--heatmap-scale-60', '#83b4c5') : '#83b4c5'],
            [1.0, (window.stylingUtils && window.stylingUtils.getColor) ? window.stylingUtils.getColor('--heatmap-scale-100', '#5a8491') : '#5a8491']
        ];

        const trace = {
            z: filteredZ,
            x: data.x,
            y: filteredY,
            type: 'heatmap',
            colorscale: novaColorScale,
            showscale: false,
            xgap: 1,
            ygap: 1,
            hovertemplate: '<b>%{y}</b><br>Week: %{x}<br>Score: %{z:.0f}/100<extra></extra>'
        };

        const calculatedHeight = Math.max(600, filteredY.length * 15);

        const layout = {
            height: calculatedHeight,
            xaxis: {
                title: '',
                side: 'top',
                tickangle: -90,
                fixedrange: true,
                tickfont: { size: 11, color: (window.stylingUtils && window.stylingUtils.getColor) ? window.stylingUtils.getColor('--text-secondary', '#555') : '#555' }
            },
            yaxis: {
                automargin: true,
                fixedrange: true,
                tickfont: { size: 11, color: (window.stylingUtils && window.stylingUtils.getColor) ? window.stylingUtils.getColor('--text-primary', '#333') : '#333' }
            },
            dragmode: false,
            margin: { l: 180, r: 20, b: 20, t: 100 },
            shapes: shapes,
            paper_bgcolor: 'rgba(0,0,0,0)',
            plot_bgcolor: 'rgba(0,0,0,0)'
        };

        const config = {
            responsive: true,
            displayModeBar: false
        };

        if (typeof Plotly === 'undefined') {
            plotDiv.innerHTML = '<div style="color:orange; text-align:center; padding:20px;">Plotly library not loaded. Please check your internet connection or ad-blocker.</div>';
            return;
        }

        Plotly.newPlot(plotDiv, [trace], layout, config).then(() => {
            heatmapLoaded = true;
            plotDiv.removeAllListeners('plotly_click');
            plotDiv.on('plotly_click', function(evt){
                if(evt.points && evt.points.length > 0) {
                    const point = evt.points[0];
                    const yIndex = currentFilteredY.indexOf(point.y);
                    const xIndex = globalHeatmapData.x.indexOf(point.x);

                    if (yIndex !== -1 && xIndex !== -1) {
                        const objectId = currentFilteredIds[yIndex];
                        if (globalHeatmapData.dates && globalHeatmapData.dates[xIndex]) {
                            const dateStr = globalHeatmapData.dates[xIndex];
                            const [year, month, day] = dateStr.split('-');
                            const currentLoc = sessionStorage.getItem('selectedLocation');
                            const locParam = currentLoc ? `&location=${encodeURIComponent(currentLoc)}` : '';
                            window.location.assign(`/graph_dashboard/${encodeURIComponent(objectId)}?tab=chart&year=${year}&month=${month}&day=${day}${locParam}`);
                        }
                    }
                }
            });
        });
    }

    function resetHeatmapState() {
        heatmapLoaded = false;
        globalHeatmapData = null;
        isFetching = false;
        const plotDiv = document.getElementById('yearly-heatmap-plot');
        if(plotDiv) plotDiv.innerHTML = "";
    }

    // Expose functions needed by other scripts
    window.updateHeatmapFilter = updateHeatmapFilter;
    window.fetchAndRenderHeatmap = fetchAndRenderHeatmap;
    window.resetHeatmapState = resetHeatmapState;

    // ============================================
    // THEME INTEGRATION
    // ============================================

    /**
     * Update heatmap with theme-aware colors
     * Re-renders the heatmap with new background and text colors
     */
    function updateHeatmapForTheme() {
        if (!globalHeatmapData || !currentFilteredY) return;

        const isDark = window.stylingUtils && window.stylingUtils.isDarkTheme
            ? window.stylingUtils.isDarkTheme()
            : false;

        // Theme-aware background colors (transparent for plot area)
        const plotBgColor = isDark ? '#1e1e1e' : '#ffffff';
        const paperBgColor = isDark ? '#121212' : '#ffffff';
        const textColor = isDark ? '#e0e0e0' : '#333';
        const tickColor = isDark ? '#b0b0b0' : '#666';

        // Update color scale for heatmap (uses CSS variables that change with theme)
        const novaColorScale = [
            [0.0, (window.stylingUtils && window.stylingUtils.getColor) ? window.stylingUtils.getColor('--heatmap-scale-0', '#1a1a1a') : '#1a1a1a'],
            [0.1, (window.stylingUtils && window.stylingUtils.getColor) ? window.stylingUtils.getColor('--heatmap-scale-10', '#2a3a3d') : '#2a3a3d'],
            [0.3, (window.stylingUtils && window.stylingUtils.getColor) ? window.stylingUtils.getColor('--heatmap-scale-30', '#3d5a61') : '#3d5a61'],
            [0.6, (window.stylingUtils && window.stylingUtils.getColor) ? window.stylingUtils.getColor('--heatmap-scale-60', '#83b4c5') : '#83b4c5'],
            [1.0, (window.stylingUtils && window.stylingUtils.getColor) ? window.stylingUtils.getColor('--heatmap-scale-100', '#a8d4e0') : '#a8d4e0']
        ];

        const trace = {
            z: currentFilteredZ,
            x: globalHeatmapData.x,
            y: currentFilteredY,
            type: 'heatmap',
            colorscale: novaColorScale,
            showscale: false,
            xgap: 1,
            ygap: 1,
            hovertemplate: '<b>%{y}</b><br>Week: %{x}<br>Score: %{z:.0f}/100<extra></extra>'
        };

        const calculatedHeight = Math.max(600, currentFilteredY.length * 15);

        const layout = {
            height: calculatedHeight,
            xaxis: {
                title: '',
                side: 'top',
                tickangle: -90,
                fixedrange: true,
                tickfont: { size: 11, color: tickColor }
            },
            yaxis: {
                automargin: true,
                fixedrange: true,
                tickfont: { size: 11, color: textColor }
            },
            dragmode: false,
            margin: { l: 180, r: 20, b: 20, t: 100 },
            paper_bgcolor: paperBgColor,
            plot_bgcolor: plotBgColor
        };

        const config = {
            responsive: true,
            displayModeBar: false
        };

        const plotDiv = document.getElementById('yearly-heatmap-plot');
        if (!plotDiv) return;

        if (typeof Plotly === 'undefined') {
            plotDiv.innerHTML = '<div style="color:orange; text-align:center; padding:20px;">Plotly library not loaded. Please check your internet connection or ad-blocker.</div>';
            return;
        }

        // Re-plot the heatmap with new theme colors
        Plotly.newPlot(plotDiv, [trace], layout, config).then(() => {
            // Re-attach click listener
            plotDiv.removeAllListeners('plotly_click');
            plotDiv.on('plotly_click', function(evt){
                if(evt.points && evt.points.length > 0) {
                    const point = evt.points[0];
                    const yIndex = currentFilteredY.indexOf(point.y);
                    const xIndex = globalHeatmapData.x.indexOf(point.x);

                    if (yIndex !== -1 && xIndex !== -1) {
                        const objectId = currentFilteredIds[yIndex];
                        if (globalHeatmapData.dates && globalHeatmapData.dates[xIndex]) {
                            const dateStr = globalHeatmapData.dates[xIndex];
                            const [year, month, day] = dateStr.split('-');
                            const currentLoc = sessionStorage.getItem('selectedLocation');
                            const locParam = currentLoc ? `&location=${encodeURIComponent(currentLoc)}` : '';
                            window.location.assign(`/graph_dashboard/${encodeURIComponent(objectId)}?tab=chart&year=${year}&month=${month}&day=${day}${locParam}`);
                        }
                    }
                }
            });
        });
    }

    // Register theme change callback
    if (window.stylingUtils && window.stylingUtils.onThemeChange) {
        window.stylingUtils.onThemeChange(function(event) {
            console.log('[heatmap_section.js] Theme changed to:', event.detail.theme);
            updateHeatmapForTheme();
        });

        // Initial update when stylingUtils is available
        // Wait a bit for heatmap to be initialized
        setTimeout(updateHeatmapForTheme, 100);
    }

    // ============================================
    // END THEME INTEGRATION
    // ============================================

    // Expose theme update function for external calls
    window.updateHeatmapForTheme = updateHeatmapForTheme;

})();

