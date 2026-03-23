(function() {
    'use strict';

    // ==========================================================================
    // COLOR HELPER FUNCTIONS
    // ==========================================================================

    /**
     * Get a CSS variable value or fallback to default
     * @param {string} varName - CSS variable name (e.g., '--primary-color')
     * @param {string} fallback - Fallback value
     * @returns {string} The CSS variable value or fallback
     */
    function getColor(varName, fallback) {
        if (window.stylingUtils && window.stylingUtils.getCssVar) {
            return window.stylingUtils.getCssVar(varName, fallback);
        }
        return fallback;
    }

    /**
     * Get primary color
     * @returns {string} Primary color value
     */
    function getPrimaryColor() {
        if (window.stylingUtils && window.stylingUtils.getPrimaryColor) {
            return window.stylingUtils.getPrimaryColor();
        }
        return '#83b4c5';
    }

    /**
     * Get text primary color
     * @returns {string} Text primary color value
     */
    function getTextPrimaryColor() {
        return getColor('--text-primary', '#333');
    }

    /**
     * Get text secondary color
     * @returns {string} Text secondary color value
     */
    function getTextSecondaryColor() {
        return getColor('--text-secondary', '#555');
    }

    /**
     * Get success color
     * @returns {string} Success color value
     */
    function getSuccessColor() {
        return getColor('--success-color', '#28a745');
    }

    /**
     * Get danger color
     * @returns {string} Danger color value
     */
    function getDangerColor() {
        return getColor('--danger-color', '#dc3545');
    }

    /**
     * Get danger text color
     * @returns {string} Danger text color value
     */
    function getDangerTextColor() {
        return getColor('--danger-text', '#721c24');
    }

    /**
     * Get danger background color
     * @returns {string} Danger background color value
     */
    function getDangerBgColor() {
        return getColor('--danger-bg', '#f8d7da');
    }

    /**
     * Get white background color
     * @returns {string} White background color value
     */
    function getBgWhiteColor() {
        return getColor('--bg-white', '#ffffff');
    }

    /**
     * Get light background color
     * @returns {string} Light background color value
     */
    function getBgLightColor() {
        return getColor('--bg-light', '#f8f9fa');
    }

    /**
     * Get border light color
     * @returns {string} Border light color value
     */
    function getBorderLightColor() {
        return getColor('--border-light', '#eee');
    }

    // ========================================================================
        // Configuration & Global State Variables
        // ========================================================================
        const IS_GUEST_USER = window.NOVA_INDEX.isGuest;
        const HIDE_INVISIBLE_PREF = window.NOVA_INDEX.hideInvisible;
        let activeTab = 'position'; // Always start on Dashboard tab
        let outlookDataLoaded = false;
        let activeFetchController = null; // Controls network cancellation
        let dataUpdateIntervalId = null; // 60-second interval for data updates
        let timerUpdateIntervalId = null; // 1-second interval for countdown display
        let askNovaController = null; // AbortController for Ask Nova requests
        let askNovaTimeoutId = null; // Timeout ID for Ask Nova requests
    
        // --- ADDED FOR SAVED VIEWS ---
        let savedViewsDropdown, saveViewBtn, deleteViewBtn;
        let allSavedViews = {}; // Global cache for view settings
        // --- END OF ADD ---
    
        let allOutlookOpportunities = [];
        window.latestDSOData = []; // <--- EXPOSE DATA GLOBALLY for Inspiration Tab
        let currentOutlookSort = { columnKey: 'date', ascending: true };
        let novaRankMap = {}; // <--- NOVA RANKING PERSISTENCE - stores object names (uppercase) to rank data

        // Restore novaRankMap from sessionStorage on page load
        const _savedRankMap = sessionStorage.getItem('novaRankMap');
        if (_savedRankMap) {
            try { novaRankMap = JSON.parse(_savedRankMap); } catch(e) { novaRankMap = {}; }
        }

        const outlookColumnConfig = {
            'object_name':  { dataKey: 'object_name',  sortable: true, filterable: true, numeric: false },
            'common_name':  { dataKey: 'common_name',  sortable: true, filterable: true, numeric: false },
            'date':         { dataKey: 'date',    sortable: true, filterable: true, numeric: false },
            'max_alt':      { dataKey: 'max_alt',      sortable: true, filterable: true, numeric: true },
            'obs_dur':      { dataKey: 'obs_dur',      sortable: true, filterable: true, numeric: true },
            'rating':       { dataKey: 'rating',       sortable: true, filterable: true, numeric: false },
            'score':        { dataKey: 'score',        sortable: true, filterable: false, numeric: true },
            'type':         { dataKey: 'type' },
            'constellation':{ dataKey: 'constellation' },
            'magnitude':    { dataKey: 'magnitude', numeric: true },
            'size':         { dataKey: 'size', numeric: true },
            'sb':           { dataKey: 'sb', numeric: true }
        };
    
        // --- DSO Table Configuration ---
        let currentSort = { columnKey: 'Altitude Current', ascending: false };
        const columnConfig = {
            'Nova Rank':        { header: '★<br><span class="subtext">&nbsp;</span>', dataKey: 'Nova Rank', type: 'always-visible', filterable: false, sortable: true },
            'Object':           { header: 'Object<br><span class="subtext">&nbsp;</span>', dataKey: 'Object', type: 'always-visible', filterable: true, sortable: true },
            'Common Name':      { header: 'Common Name<br><span class="subtext">&nbsp;</span>', dataKey: 'Common Name', type: 'always-visible', filterable: true, sortable: true },
            'Altitude Current': { header: 'Altitude<br><span class="subtext">(Current)</span>', dataKey: 'Altitude Current', type: 'position', filterable: true, sortable: true, format: val => (val === 'N/A' || val === null || val === undefined) ? 'N/A' : `${parseFloat(val).toFixed(2)}°` },
            'Azimuth Current':  { header: 'Azimuth <br><span class="subtext">(Current)</span>', dataKey: 'Azimuth Current', type: 'position', filterable: true, sortable: true, format: val => (val === 'N/A' || val === null || val === undefined) ? 'N/A' : `${parseFloat(val).toFixed(2)}°` },
            'Trend':            { header: 'Trend<br><span class="subtext">&nbsp;</span>', dataKey: 'Trend', type: 'position', filterable: false, sortable: true },
            'Altitude 11PM':    { header: 'Altitude <br><span class="subtext">(11 PM)</span>', dataKey: 'Altitude 11PM', type: 'position', filterable: true, sortable: true, format: val => (val === 'N/A' || val === null || val === undefined) ? 'N/A' : `${parseFloat(val).toFixed(2)}°` },
            'Azimuth 11PM':     { header: 'Azimuth <br><span class="subtext">(11 PM)</span>', dataKey: 'Azimuth 11PM', type: 'position', filterable: true, sortable: true, format: val => (val === 'N/A' || val === null || val === undefined) ? 'N/A' : `${parseFloat(val).toFixed(2)}°` },
            'Transit Time':     { header: 'Transit <br><span class="subtext">(Local Time)</span>', dataKey: 'Transit Time', type: 'position', filterable: false, sortable: true },
            'Observable Duration (min)': { header: 'Observable <br><span class="subtext">(minutes)</span>', dataKey: 'Observable Duration (min)', type: 'position', filterable: true, sortable: true, format: val => (val === 'N/A' || val === null || val === undefined) ? 'N/A' : String(val) },
            'Max Altitude (°)': { header: 'Max Altitude<br><span class="subtext">observable (°)</span>', dataKey: 'Max Altitude (°)', type: 'position', filterable: true, sortable: true, format: val => (val === 'N/A' || val === null || val === undefined || isNaN(Number(val))) ? 'N/A' : `${Number(val).toFixed(1)}°` },
            'Angular Separation (°)': { header: 'Ang. Sep. <br><span class="subtext">to moon (°)</span>', dataKey: 'Angular Separation (°)', type: 'position', filterable: true, sortable: true, format: val => (val === 'N/A' || val === null || val === undefined || isNaN(Number(val))) ? 'N/A' : `${parseInt(val)}°` },
            'Constellation':    { header: 'Con<br><span class="subtext">&nbsp;</span>', dataKey: 'Constellation', type: 'properties', filterable: true, sortable: true },
            'Type':             { header: 'Type<br><span class="subtext">&nbsp;</span>', dataKey: 'Type', type: 'properties', filterable: true, sortable: true },
            'Magnitude':        { header: 'Magnitude<br><span class="subtext">&nbsp;</span>', dataKey: 'Magnitude', type: 'properties', filterable: true, sortable: true, format: val => (val === 'N/A' || val === null || val === undefined || isNaN(Number(val))) ? 'N/A' : parseFloat(val).toFixed(1) },
            'Size':             { header: "Size (')<br><span class='subtext'>&nbsp;</span>", dataKey: 'Size', type: 'properties', filterable: true, sortable: true, format: val => (val === 'N/A' || val === null || val === undefined || isNaN(Number(val))) ? 'N/A' : parseFloat(val).toFixed(1) },
            'SB':               { header: 'SB<br><span class="subtext">&nbsp;</span>', dataKey: 'SB', type: 'properties', filterable: true, sortable: true, format: val => (val === 'N/A' || val === null || val === undefined || isNaN(Number(val))) ? 'N/A' : parseFloat(val).toFixed(1) },
            'Best Month':       { header: 'Best Month<br><span class="subtext">(Opp.)</span>', dataKey: 'best_month_ra', type: 'properties', filterable: true, sortable: true },
            'Max Altitude':     { header: 'Max Alt<br><span class="subtext">(Culm.)</span>', dataKey: 'max_culmination_alt', type: 'properties', filterable: true, sortable: true, format: val => (val === 'N/A' || val === null || val === undefined || isNaN(Number(val))) ? 'N/A' : `${Number(val).toFixed(1)}°` }
        };
    
        // --- Journal Table Configuration ---
        const allJournalSessions = window.NOVA_INDEX.journalSessions;
        let currentJournalSort = { columnKey: 'date_utc', ascending: false };
        const journalColumnConfig = {
            'object_name': {
                headerText: 'Object',
                dataKey: 'object_name',
                sortable: true,
                filterable: true
            },
            'target_common_name': {
                headerText: 'Common Name',
                dataKey: 'target_common_name',
                sortable: true,
                filterable: true
            },
            // --- MOVED: Project is now here ---
            'project_name': {
                headerText: 'Project',
                dataKey: 'project_name',
                sortable: true,
                filterable: true
            },
            'date_utc': {
                headerText: 'Date',
                dataKey: 'date_utc',
                sortable: true,
                filterable: true,
                format: formatDateISOtoEuropean
            },
            'location_name': {
                headerText: 'Location',
                dataKey: 'location_name',
                sortable: true,
                filterable: true
            },
            'telescope_setup_notes': {
                headerText: 'Telescope Setup',
                dataKey: 'telescope_setup_notes',
                sortable: true,
                filterable: true,
                format: val => (val === null || val === undefined || String(val).trim() === '') ? 'N/A' : String(val).substring(0, 60) + (String(val).length > 60 ? '...' : '')
            },
            'calculated_integration_time_minutes': {
                headerText: 'Total Integration',
                dataKey: 'calculated_integration_time_minutes',
                sortable: true,
                filterable: true,
                format: val => (val === null || val === undefined || isNaN(Number(val))) ? 'N/A' : `${Number(val).toFixed(0)} min`
            },
            'session_rating_subjective': {
                headerText: 'Rating',
                dataKey: 'session_rating_subjective',
                sortable: true,
                filterable: true,
                format: val => {
                    if (val === null || val === undefined) return 'N/A';
                    const num = parseInt(val, 10);
                    if (isNaN(num) || num < 1) return '☆☆☆☆☆';
                    const stars = Math.min(num, 5);
                    return '★'.repeat(stars) + '☆'.repeat(5 - stars);
                }
            }
        };
    
        // ========================================================================
        // Simulation Mode Functions
        // ========================================================================
        function initializeSimulationMode() {
            const simModeToggle = document.getElementById('sim-mode-toggle');
            const simDateInput = document.getElementById('sim-date-input');
            const simulatedTitleText = document.getElementById('simulated-title-text');
            const statusStrip = document.querySelector('.status-strip');
            const updateStatusItem = document.querySelector('.status-item[style*="margin-left: auto"]');
            const updateLabel = updateStatusItem?.querySelector('.status-label');
            const updateValue = document.getElementById('next-update-timer');
    
            function applySimState(isSim, dateVal) {
                if (isSim && dateVal) {
                    simDateInput.disabled = false;
                    simDateInput.value = dateVal;
                    statusStrip.classList.add('simulated');
                    simulatedTitleText.style.display = 'inline';
                    if(updateLabel) updateLabel.textContent = window.t('mode');
                    if(updateValue) {
                        updateValue.textContent = window.t('simulated');
                        updateValue.style.color = getDangerColor(); // Red color for simulated text
                    }
                } else {
                    simDateInput.disabled = true;
                    statusStrip.classList.remove('simulated');
                    simulatedTitleText.style.display = 'none';
                    // Don't set updateLabel text - the timer value already shows "Update in Xs"
                    if(updateValue) {
                        updateValue.style.color = getPrimaryColor(); // Restore original color
                    }
                    // The timer interval will take care of resetting the value text
                }
            }
    
            function updateDataForSim() {
                const currentSelectedLocation = sessionStorage.getItem('selectedLocation');
                // Clear current cache keys to force fresh calculation with the simulated date
                for (let i = 0; i < sessionStorage.length; i++) {
                    const key = sessionStorage.key(i);
                    if (key.startsWith('nova_desktop_cache_')) {
                        sessionStorage.removeItem(key);
                    }
                }
                // Reset outlook flag so it re-calculates for the new reference date
                outlookDataLoaded = false;
                fetchData();
                fetchSunEvents();
            }
    
            simModeToggle.addEventListener('change', function() {
                const isChecked = this.checked;
                const hadExistingDate = !!simDateInput.value;  // Check BEFORE we auto-fill
                if (isChecked && !simDateInput.value) {
                    const today = new Date();
                    const yyyy = today.getFullYear();
                    const mm = String(today.getMonth() + 1).padStart(2, '0');
                    const dd = String(today.getDate()).padStart(2, '0');
                    simDateInput.value = `${yyyy}-${mm}-${dd}`;
                }
                localStorage.setItem('simModeActive', isChecked);
                localStorage.setItem('simDate', simDateInput.value);
                applySimState(isChecked, simDateInput.value);
                // Only trigger data fetch if a date was already set (e.g., from previous session).
                // If we just auto-filled today's date, wait for the user to set their desired date.
                if (hadExistingDate) {
                    updateDataForSim();
                }
            });
    
            simDateInput.addEventListener('change', function() {
                if (simModeToggle.checked) {
                    localStorage.setItem('simDate', this.value);
                    applySimState(true, this.value);
                    updateDataForSim();
                }
            });
    
            // On load - restore saved simulation mode state if available
            const savedSimMode = localStorage.getItem('simModeActive') === 'true';
            const savedSimDate = localStorage.getItem('simDate');
            if (savedSimMode && savedSimDate) {
                simModeToggle.checked = true;
                simDateInput.value = savedSimDate;
                applySimState(true, savedSimDate);
                // Update data for simulation mode on load
                updateDataForSim();
            } else {
                simModeToggle.checked = false;
                applySimState(false, null);
            }
        }
    
        // ========================================================================
        // Helper Functions
        // ========================================================================
        function parseTimeToMinutes(timeStr) {
          if (!timeStr || typeof timeStr !== 'string' || !/^\d{1,2}:\d{2}$/.test(timeStr)) return 0;
          const [h, m] = timeStr.split(':').map(Number);
          return h * 60 + m;
        }
        function updateRemoveFiltersButtonVisibility() {
            const btnContainer = document.getElementById('remove-filters-container');
            if (!btnContainer) return;

            let isAnyFilterActive = false;
            const allFilterInputs = document.querySelectorAll('#data-table .filter-row input, #journal-filter-row input');

            for (const input of allFilterInputs) {
                if (input.value.trim() !== '') {
                    isAnyFilterActive = true;
                    break;
                }
            }

            // --- FIX: Also show button if a Saved View is active ---
            const svDropdown = document.getElementById('saved-views-dropdown');
            if (svDropdown && svDropdown.value !== "") {
                isAnyFilterActive = true;
            }
            // --- END FIX ---

            // --- FIX: Also show button if Nova ranking is active ---
            if (Object.keys(novaRankMap).length > 0) {
                isAnyFilterActive = true;
            }
            // --- END FIX ---

            btnContainer.style.display = isAnyFilterActive ? 'block' : 'none';
        }
    
        function clearAllFilters() {
            // Clear DSO filters
            for (const key in columnConfig) {
                if (columnConfig[key].filterable) {
                    const inputEl = document.querySelector(`#data-table .filter-row th[data-column-key="${key}"] input`);
                    if (inputEl) inputEl.value = '';
                    localStorage.removeItem("dso_filter_col_key_" + key);
                }
            }
    
            // Clear Journal filters
            for (const key in journalColumnConfig) {
                if (journalColumnConfig[key].filterable) {
                    const inputEl = document.querySelector(`#journal-filter-row th[data-journal-column-key="${key}"] input`);
                    if (inputEl) inputEl.value = '';
                    localStorage.removeItem("journal_filter_col_key_" + key);
                }
            }
    
            // --- MODIFIED: Reset saved views dropdown AND clear persistent view name ---
            if (savedViewsDropdown) savedViewsDropdown.value = '';
            if (deleteViewBtn) deleteViewBtn.style.display = 'none';
            localStorage.removeItem('nova_last_applied_view');
    
            // --- FIX: If on Heatmap, force a re-render to show "All" ---
            if (activeTab === 'heatmap' && typeof updateHeatmapFilter === 'function') {
                updateHeatmapFilter();
            }
            // --- END FIX ---

            // Refresh the tables to reflect the cleared filters
            filterTable();
            filterJournalTable();

            // --- Clear Nova ranking if badges are present ---
            if (Object.keys(novaRankMap).length > 0) {
                resetRanking();
            }

            // Ensure button state updates immediately (after clearing)
            updateRemoveFiltersButtonVisibility();
        }
    
        // ========================================================================
        // Saved Views Functions (NEW - DB Version)
        // ========================================================================
    
        async function populateSavedViewsDropdown(selectedViewName = null) {
            if (!savedViewsDropdown) return;
    
            // --- THIS BLOCK IS NEW ---
            if (IS_GUEST_USER) {
                savedViewsDropdown.innerHTML = `<option value="">${window.t('saved_views_placeholder')}</option>`;
                savedViewsDropdown.disabled = true;
                if (saveViewBtn) saveViewBtn.style.display = 'none';
                if (deleteViewBtn) deleteViewBtn.style.display = 'none';
                return; // Stop here for guest users
            }
            // --- END OF NEW BLOCK ---
    
            try {
                const response = await fetch('/api/get_saved_views');
                if (!response.ok) throw new Error('Failed to fetch views');
                allSavedViews = await response.json(); // Store in local variable
                window.allSavedViews = allSavedViews;  // FIX: Expose to Heatmap script
    
                savedViewsDropdown.innerHTML = `<option value="">${window.t('saved_views_placeholder')}</option>`;
                const viewNames = Object.keys(allSavedViews).sort((a, b) => a.toLowerCase().localeCompare(b.toLowerCase()));
    
                viewNames.forEach(viewName => {
                    const option = document.createElement('option');
                    option.value = viewName;
                    option.textContent = viewName;
                    savedViewsDropdown.appendChild(option);
                });
    
                if (selectedViewName && allSavedViews[selectedViewName]) {
                    savedViewsDropdown.value = selectedViewName;
                    if (deleteViewBtn) deleteViewBtn.style.display = 'inline-block';
                } else {
                    if (deleteViewBtn) deleteViewBtn.style.display = 'none';
                }
            } catch (error) {
                console.error("Error populating saved views:", error);
                savedViewsDropdown.innerHTML = `<option value="">${window.t('error_loading_views')}</option>`;
            }
        }
    
        function getCurrentSettingsSnapshot() {
            const settings = {};
            // Get DSO filters
            for (const key in columnConfig) {
                if (columnConfig[key].filterable) {
                    settings['dso_filter_col_key_' + key] = localStorage.getItem('dso_filter_col_key_' + key) || '';
                }
            }
            // Get Journal filters
            for (const key in journalColumnConfig) {
                if (journalColumnConfig[key].filterable) {
                    settings['journal_filter_col_key_' + key] = localStorage.getItem('journal_filter_col_key_' + key) || '';
                }
            }
            // Get DSO sort
            settings['dso_sortColumnKey'] = localStorage.getItem('dso_sortColumnKey') || 'Altitude Current';
            settings['dso_sortOrder'] = localStorage.getItem('dso_sortOrder') || 'desc';
            // Get Journal sort
            settings['journal_sortColumnKey'] = localStorage.getItem('journal_sortColumnKey') || 'session_date';
            settings['journal_sortOrder'] = localStorage.getItem('journal_sortOrder') || 'desc';
            // Get active tab
            settings['activeTab'] = localStorage.getItem('activeTab') || 'position';
            return settings;
        }
    
        function loadView(viewName) {
            const viewData = allSavedViews[viewName];
            if (!viewData || !viewData.settings) {
                alert('Error: Could not load view data from cache.');
                return;
            }
            // 1. Clear all existing filters from inputs and localStorage
            clearAllFilters();
    
            // 2. Apply all settings from the snapshot to localStorage
            for (const key in viewData.settings) {
                // Prevent the saved view from overwriting your current tab
                if (key === 'activeTab') continue;
                localStorage.setItem(key, viewData.settings[key]);
            }
    
            // Explicitly ensure the current tab persists across the reload
            localStorage.setItem('activeTab', activeTab);
    
            // 3. Update the dropdown selection immediately
            if (savedViewsDropdown) {
                savedViewsDropdown.value = viewName;
                if (deleteViewBtn) deleteViewBtn.style.display = 'inline-block';
            }
            localStorage.setItem('nova_last_applied_view', viewName);
    
            // 4. Update memory flags and refresh UI without full page reload
            // activeTab = localStorage.getItem('activeTab') || 'position';  // Always use default (Dashboard tab)
    
            // Restore inputs from localStorage for both tables
            for (const key in columnConfig) {
                if (columnConfig[key].filterable) {
                    const val = localStorage.getItem("dso_filter_col_key_" + key);
                    const inputEl = document.querySelector(`#data-table .filter-row th[data-column-key="${key}"] input`);
                    if (inputEl) inputEl.value = val || '';
                }
            }
            for (const key in journalColumnConfig) {
                if (journalColumnConfig[key].filterable) {
                    const val = localStorage.getItem("journal_filter_col_key_" + key);
                    const inputEl = document.querySelector(`#journal-filter-row th[data-journal-column-key="${key}"] input`);
                    if (inputEl) inputEl.value = val || '';
                }
            }
    
            // Restore sort state variables - always use default (Altitude descending)
            // const storedDsoSort = localStorage.getItem('dso_sortColumnKey');
            // if (storedDsoSort) {
            //     currentSort.columnKey = storedDsoSort;
            //     currentSort.ascending = (localStorage.getItem('dso_sortOrder') === 'asc');
            // }
    
            // Trigger UI updates
            filterTable();
            filterJournalTable();
            sortTable(currentSort.columnKey, false);
            updateTabDisplay();
            updateRemoveFiltersButtonVisibility();
    
            // If on Inspiration tab, refresh the grid
            if (activeTab === 'inspiration' && typeof renderInspirationGrid === 'function') {
                renderInspirationGrid();
            }
        }
    
        // Updated to open modal
        function handleSaveView() {
            const modal = document.getElementById('save-view-modal');
            document.getElementById('modal-view-name').value = '';
            document.getElementById('modal-view-desc').value = '';
            if(document.getElementById('modal-view-shared')) document.getElementById('modal-view-shared').checked = false;
            modal.style.display = 'block';
        }
    
        function closeSaveViewModal() {
            document.getElementById('save-view-modal').style.display = 'none';
        }
    
        async function confirmSaveView() {
            const viewName = document.getElementById('modal-view-name').value.trim();
            const description = document.getElementById('modal-view-desc').value.trim();
            const isShared = document.getElementById('modal-view-shared') ? document.getElementById('modal-view-shared').checked : false;
    
            if (!viewName) { alert(window.t('name_required')); return; }
    
            closeSaveViewModal();
            const currentSettings = getCurrentSettingsSnapshot();
    
            try {
                const response = await fetch('/api/save_saved_view', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        name: viewName,
                        description: description,
                        is_shared: isShared,
                        settings: currentSettings
                    })
                });
    
                if (!response.ok) {
                    const err = await response.json();
                    throw new Error(err.message || 'Failed to save view');
                }
    
                // Repopulate the dropdown, which will fetch the new list and select the new view
                await populateSavedViewsDropdown(viewName);
    
            } catch (error) {
                console.error("Error saving view:", error);
                alert(`Error saving view: ${error.message}`);
            }
        }
    
        async function handleDeleteView() {
            const viewName = savedViewsDropdown.value;
            if (!viewName) return;
    
            if (!confirm(`Are you sure you want to delete the view "${viewName}"?`)) return;
    
            try {
                const response = await fetch('/api/delete_saved_view', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: viewName })
                });
    
                if (!response.ok) {
                    const err = await response.json();
                    throw new Error(err.message || 'Failed to delete view');
                }
    
                // Repopulate the dropdown (will reset to default)
                await populateSavedViewsDropdown();
                if (deleteViewBtn) deleteViewBtn.style.display = 'none';
    
            } catch (error) {
                console.error("Error deleting view:", error);
                alert(`Error deleting view: ${error.message}`);
            }
        }
    
        // ========================================================================
        // Tab Display Logic
        // ========================================================================
        function updateTabDisplay() {
            const dsoTableWrapper = document.getElementById('dso-table-wrapper');
            const dsoLoadingDiv = document.getElementById('table-loading');
            const journalTableWrapper = document.getElementById('journal-table-wrapper');
            const outlookWrapper = document.getElementById('outlook-wrapper');
            const heatmapWrapper = document.getElementById('heatmap-tab-content');
            const inspirationWrapper = document.getElementById('inspiration-tab-content'); // NEW
    
            document.querySelectorAll('.tab-button').forEach(button => {
                button.classList.toggle('active', button.dataset.tab === activeTab);
            });
    
            if (dsoTableWrapper) dsoTableWrapper.style.display = 'none';
            if (dsoLoadingDiv) dsoLoadingDiv.style.display = 'none';
            if (journalTableWrapper) journalTableWrapper.style.display = 'none';
            if (outlookWrapper) outlookWrapper.style.display = 'none';
            if (heatmapWrapper) heatmapWrapper.style.display = 'none';
            if (inspirationWrapper) inspirationWrapper.style.display = 'none'; // NEW
    
            if (dsoLoadingDiv) dsoLoadingDiv.style.display = 'none';
    
            if (activeTab === 'position' || activeTab === 'properties') {
                if (dsoTableWrapper) dsoTableWrapper.style.display = 'block';
                applyDsoColumnVisibility();
                const dsoTableBody = document.getElementById('data-body');
                if (dsoTableBody && dsoTableBody.innerHTML.trim() === '') {
                    if (dsoLoadingDiv) dsoLoadingDiv.style.display = 'block';
                }
            } else if (activeTab === 'journal') {
                if (journalTableWrapper) journalTableWrapper.style.display = 'block';
                populateJournalTable();
            } else if (activeTab === 'outlook') {
                if (outlookWrapper) outlookWrapper.style.display = 'block';
                if (!outlookDataLoaded) fetchOutlookData();
            } else if (activeTab === 'heatmap') {
                if (heatmapWrapper) heatmapWrapper.style.display = 'block';
                if (typeof fetchAndRenderHeatmap === 'function') {
                    fetchAndRenderHeatmap();
                }
            } else if (activeTab === 'inspiration') { // NEW BLOCK
                if (inspirationWrapper) inspirationWrapper.style.display = 'block';
                if (typeof renderInspirationGrid === 'function') {
                    renderInspirationGrid();
                }
            }
    
            localStorage.setItem('activeTab', activeTab);
            updateRemoveFiltersButtonVisibility();
        }
    
        function applyDsoColumnVisibility() {
            const headers = document.querySelectorAll("#data-table > thead > tr:not(.filter-row) > th[data-column-key]");
            const filterCells = document.querySelectorAll("#data-table .filter-row th[data-column-key]");
            const tableBodyRows = document.querySelectorAll("#data-body tr");
    
            headers.forEach(th => {
                const columnKey = th.dataset.columnKey;
                const config = columnConfig[columnKey];
                if (config) {
                    th.style.display = (config.type === 'always-visible' || config.type === activeTab) ? 'table-cell' : 'none';
                }
            });
            filterCells.forEach(thFilter => {
                const columnKey = thFilter.dataset.columnKey;
                const config = columnConfig[columnKey];
                const input = thFilter.querySelector('input');
                let show = false;
                if (config) {
                    show = (config.type === 'always-visible' || config.type === activeTab);
                    thFilter.style.display = show ? 'table-cell' : 'none';
                    if (input) {
                        input.disabled = !show || !config.filterable;
                        input.style.visibility = show && config.filterable ? 'visible' : 'hidden';
                    }
                }
            });
            tableBodyRows.forEach(row => {
                const cells = row.querySelectorAll('td[data-column-key]');
                cells.forEach(td => {
                    const columnKey = td.dataset.columnKey;
                    const config = columnConfig[columnKey];
                    if (config) {
                        td.style.display = (config.type === 'always-visible' || config.type === activeTab) ? 'table-cell' : 'none';
                    }
                });
            });
        }
    
        // ========================================================================
        // DSO Table Functions (NEW, CORRECTED VERSION)
        // ========================================================================
    
        // Helper: match numeric filters like ">170<190", ">=20 <=50", or ranges like "170-190" / "170..190"
        function matchesNumericFilter(cellNumber, filterValue) {
            if (typeof cellNumber !== 'number' || isNaN(cellNumber)) return false;
            if (!filterValue || typeof filterValue !== 'string') return false;
            let s = filterValue.trim();
            // Normalize separators
            s = s.replace(/,/g, ' ');
    
            // Case 1: explicit range "a-b" or "a..b" or "a—b"
            const rangeMatch = s.match(/^\s*(-?\d+(?:\.\d+)?)\s*[-–—:.]{1,2}\s*(-?\d+(?:\.\d+)?)\s*$/);
            if (rangeMatch) {
                let a = parseFloat(rangeMatch[1]);
                let b = parseFloat(rangeMatch[2]);
                if (isNaN(a) || isNaN(b)) return false;
                if (a > b) { const tmp = a; a = b; b = tmp; }
                return cellNumber >= a && cellNumber <= b;
            }
    
            // Case 2: chained comparators like ">170<190" or ">=20 <=50"
            const pairs = [];
            const regex = /([<>]=?)\s*(-?\d+(?:\.\d+)?)/g;
            let m;
            while ((m = regex.exec(s)) !== null) {
                pairs.push({ op: m[1], val: parseFloat(m[2]) });
            }
            if (pairs.length > 0) {
                return pairs.every(p => {
                    if (isNaN(p.val)) return false;
                    if (p.op === '>')  return cellNumber >  p.val;
                    if (p.op === '>=') return cellNumber >= p.val;
                    if (p.op === '<')  return cellNumber <  p.val;
                    if (p.op === '<=') return cellNumber <= p.val;
                    return false;
                });
            }
    
            // Fallback: simple substring match on the numeric text
            return String(cellNumber).indexOf(s) !== -1;
        }
    
    
    // ========================================================================
    // Private Helper Functions for fetchData()
    // ========================================================================

    /**
     * Check sessionStorage for cached data
     * @param {string} cacheKey - The cache key to check
     * @param {number} expiryMs - Cache expiry time in milliseconds
     * @returns {Array|null} - Cached data if valid, null otherwise
     */
    function _checkFetchCache(cacheKey, expiryMs) {
        try {
            const raw = sessionStorage.getItem(cacheKey);
            if (raw) {
                const parsed = JSON.parse(raw);
                if (Date.now() - parsed.timestamp < expiryMs) {
                    return parsed.data;
                }
            }
        } catch(e) {
            console.warn('Cache read error:', e);
        }
        return null;
    }

    /**
     * Save data to sessionStorage cache
     * @param {string} cacheKey - The cache key
     * @param {Array} data - The data to cache
     */
    function _saveFetchCache(cacheKey, data) {
        try {
            sessionStorage.setItem(cacheKey, JSON.stringify({
                timestamp: Date.now(),
                data: data
            }));
        } catch (e) {
            console.warn("Cache full, could not save:", e);
        }
    }

    /**
     * Show and initialize the loading UI
     * @param {HTMLElement} loadingDiv - The loading container
     * @param {HTMLElement} progressBar - The progress bar element
     * @param {HTMLElement} loadingMessage - The loading message element
     */
    function _showFetchLoader(loadingDiv, progressBar, loadingMessage) {
        if (loadingDiv) loadingDiv.style.display = 'block';
        if (loadingMessage) loadingMessage.textContent = window.t('calculating');
        if (progressBar) progressBar.style.width = "5%";
    }

    /**
     * Hide the loading UI
     * @param {HTMLElement} loadingDiv - The loading container
     */
    function _hideFetchLoader(loadingDiv) {
        if (loadingDiv) loadingDiv.style.display = 'none';
    }

    /**
     * Update the progress bar and count display
     * @param {HTMLElement} progressBar - The progress bar element
     * @param {HTMLElement} loadingCount - Element showing current count
     * @param {HTMLElement} loadingTotal - Element showing total count
     * @param {number} current - Current number of items loaded
     * @param {number} total - Total number of items to load
     */
    function _updateFetchProgress(progressBar, loadingCount, loadingTotal, current, total) {
        if (progressBar) {
            const percent = Math.min(100, Math.round((current / total) * 100));
            progressBar.style.width = `${percent}%`;
        }
        if (loadingCount) loadingCount.textContent = current;
        if (loadingTotal) loadingTotal.textContent = total;
    }

    /**
     * Build the batch API URL with parameters
     * @param {number} offset - Pagination offset
     * @param {number} limit - Pagination limit
     * @param {string} location - Selected location
     * @param {string|null} effectiveDate - Simulation date if applicable
     * @returns {string} - Complete API URL
     */
    function _buildBatchUrl(offset, limit, location, effectiveDate) {
        let url = `/api/get_desktop_data_batch?offset=${offset}&limit=${limit}&location=${encodeURIComponent(location || '')}`;
        if (effectiveDate) {
            url += `&sim_date=${effectiveDate}`;
        }
        return url;
    }

    /**
     * Apply highlighting logic to a table cell
     * @param {HTMLElement} td - The table cell element
     * @param {string} columnKey - The column identifier
     * @param {*} rawValue - The raw cell value
     * @param {Object} objectData - Complete object data row
     * @param {number} altitudeThreshold - User's altitude threshold
     */
    function _applyRowHighlights(td, columnKey, rawValue, objectData, altitudeThreshold) {
        const config = columnConfig[columnKey];
        if (!config) return;

        const isAboveThreshold = parseFloat(rawValue) >= altitudeThreshold;

        // Helper to wrap cell content in a span.alt-badge
        function wrapInBadge(cell, value) {
            const span = document.createElement('span');
            span.className = 'alt-badge';
            span.textContent = value;
            cell.textContent = '';
            cell.appendChild(span);
        }

        if (config.dataKey === 'Altitude Current' && isAboveThreshold && !objectData.error) {
            td.classList.add('highlight');
            if (objectData.is_obstructed_now) td.classList.add('obstructed');
            // Wrap the altitude value in a span for the pill badge styling
            wrapInBadge(td, td.textContent);
        }

        if (config.dataKey === 'Altitude 11PM' && isAboveThreshold && !objectData.error) {
            td.classList.add('highlight');
            if (objectData.is_obstructed_at_11pm) td.classList.add('obstructed');
            // Wrap the altitude value in a span for the pill badge styling
            wrapInBadge(td, td.textContent);
        }
    }

    // ========================================================================
    // Main fetchData Function
    // ========================================================================

    // Accepted parameter: isBackground (default false)
    async function fetchData(isBackground = false) {
        const tbody = document.getElementById("data-body");
        const loadingDiv = document.getElementById("table-loading");
        const progressBar = document.getElementById("loading-progress-bar");
        const loadingCount = document.getElementById("loading-count");
        const loadingTotal = document.getElementById("loading-total");
        const loadingMessage = document.getElementById("loading-message");
    
        // Cancel previous pending requests
        if (activeFetchController) {
            activeFetchController.abort();
        }
        activeFetchController = new AbortController();
        const signal = activeFetchController.signal;
    
        tbody.dataset.loading = 'true';
    
        const currentSelectedLocation = sessionStorage.getItem('selectedLocation');
        const simModeOn = document.getElementById('sim-mode-toggle')?.checked;
        const simDateVal = document.getElementById('sim-date-input')?.value;
        const effectiveDate = simModeOn && simDateVal ? simDateVal : null;
    
        // Cache for 1 minute to match update interval
        const CACHE_KEY = `nova_desktop_cache_${currentSelectedLocation || 'default'}_${effectiveDate || 'realtime'}`;
        const CACHE_EXPIRY = 60 * 1000;
    
        console.log(`Fetch requested for location: ${currentSelectedLocation}`);
    
        // 1. Try Cache First
        const cachedData = _checkFetchCache(CACHE_KEY, CACHE_EXPIRY);
        if (cachedData) {
            if (signal.aborted) return;
            const sortedData = applyNovaRankSorting(cachedData);
            renderRows(sortedData);
            if (signal.aborted) return;
            finalizeFetch();
            return;
        }
    
        // 2. Network Fetch (BATCH MODE)
        // Fetch 50 items at a time. Server handles the loop.
        const BATCH_SIZE = 50;
        let offset = 0;
        let total = null; // Will be updated by first response (null for subsequent pages)
        let allData = [];

        // Determine if we show the loader
        const shouldShowLoader = !isBackground || tbody.innerHTML.trim() === '';
        if (shouldShowLoader) {
            _showFetchLoader(loadingDiv, progressBar, loadingMessage);
        } else {
            // SAFETY: If a background fetch takes over an aborted foreground fetch,
            // we must ensure the previous loader is hidden to prevent it from getting stuck.
            _hideFetchLoader(loadingDiv);
        }

        // Clear table ONLY if we are showing loader (fresh load)
        if (shouldShowLoader) tbody.innerHTML = '';

        try {
            while (true) {
                if (signal.aborted) return;

                // Call the new Batch Endpoint
                const url = _buildBatchUrl(offset, BATCH_SIZE, currentSelectedLocation, effectiveDate);
                const res = await fetch(url, { signal });

                if (!res.ok) throw new Error(`Server Error: ${res.status}`);

                const json = await res.json();
                // Only update total if provided (first page)
                if (json.total !== null && json.total !== undefined) {
                    total = json.total;
                }

                const chunkData = json.results || [];
                const hasMore = json.has_more === true;
                allData = allData.concat(chunkData);

                // Render this chunk immediately (Stream effect)
                if (shouldShowLoader) {
                    appendRows(chunkData);
                    // FIX: Re-apply filters immediately so the DOM state is correct
                    // This prevents the Inspiration tab from reading visible rows that should be hidden
                    filterTable();
                }

                // Break if no more results
                if (!hasMore || chunkData.length < BATCH_SIZE) {
                    break;
                }

                // Update Progress UI
                offset += BATCH_SIZE;
                if (shouldShowLoader) {
                    // Use total if available, otherwise show current count without percent
                    if (total !== null) {
                        _updateFetchProgress(progressBar, loadingCount, loadingTotal, allData.length, total);
                    } else {
                        // Show current count without percentage when total is unknown
                        if (progressBar) {
                            progressBar.style.width = '100%';
                            progressBar.classList.add('indeterminate');
                        }
                        if (loadingCount) loadingCount.textContent = allData.length;
                        if (loadingTotal) loadingTotal.textContent = '...';
                    }
                }
            }
    
            // Fetch complete - Save to Cache
            _saveFetchCache(CACHE_KEY, allData);
    
            // CRITICAL FIX: Ensure global data is always updated for other tabs
            window.latestDSOData = allData;

            // If background refresh, replace table now
            if (!shouldShowLoader) {
                const sortedData = applyNovaRankSorting(allData);
                renderRows(sortedData);
            }
            // Logic for triggering inspiration update moved to finalizeFetch
            // to ensure filters are applied first.
    
        } catch (error) {
            if (error.name !== 'AbortError') {
                console.error("Batch Fetch Error:", error);
                if(tbody.innerHTML === '') {
                    tbody.innerHTML = `<tr><td colspan="18" style="text-align:center; color:red;">${window.t('data_load_failed')}: ${error.message}</td></tr>`;
                }
                // Ensure loader is hidden on error
                _hideFetchLoader(loadingDiv);
                tbody.dataset.loading = 'false';
            }
        } finally {
            // Ensure loader is hidden and state is reset even if the controller was rotated
            if (activeFetchController && activeFetchController.signal === signal) {
                activeFetchController = null;
            }
            // Fix: Do not hide the loader or reset UI if this request was aborted (e.g., by a new fetch)
            if (!signal.aborted) {
                finalizeFetch();
            }
        }
    }  // closes fetchData

    // ========================================================================
    // "Ask Nova" Best Objects Feature
    // ========================================================================

    let originalTableData = null;
    let originalSortState = null;

    /**
     * Apply Nova rank sorting to data array before rendering
     * Returns sorted data with ranked objects first (by rank ascending), unranked objects follow in original order
     * @param {Array} data - Array of object data
     * @returns {Array} Sorted array of object data
     */
    function applyNovaRankSorting(data) {
        // If novaRankMap is empty, return data unchanged
        if (Object.keys(novaRankMap).length === 0) {
            return data;
        }

        // Separate ranked and unranked objects
        const rankedObjects = [];
        const unrankedObjects = [];

        data.forEach(obj => {
            // Normalize object name to uppercase for lookup
            const objectNameUpper = (obj.Object || '').trim().toUpperCase();
            const rankData = novaRankMap[objectNameUpper];

            if (rankData) {
                rankedObjects.push({
                    obj: obj,
                    rank: rankData.rank
                });
            } else {
                unrankedObjects.push(obj);
            }
        });

        // Sort ranked objects by rank ascending (ensure numeric comparison)
        rankedObjects.sort((a, b) => {
            const rankA = parseInt(a.rank, 10);
            const rankB = parseInt(b.rank, 10);
            return rankA - rankB;
        });

        // Return all objects: ranked first, unranked after
        return [...rankedObjects.map(item => item.obj), ...unrankedObjects];
    }

    /**
     * Apply Nova rank badges to all rows in the table

    /**
     * Show modal with Nova ranking details
     * @param {string} objectName - The object name
     * @param {number} rank - The rank number
     * @param {string} reason - The reason for the ranking
     * @param {Array} recommendedRigs - Array of recommended rig names
     */
    function showNovaRankModal(objectName, rank, reason, recommendedRigs) {
        // Ensure recommendedRigs is an array
        const rigs = Array.isArray(recommendedRigs) ? recommendedRigs : [];

        // Create modal element if it doesn't exist
        let modal = document.getElementById('nova-rank-modal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'nova-rank-modal';
            modal.className = 'modal-backdrop';
            modal.style.cssText = 'display:none; position:fixed; inset:0; background:var(--overlay-dark); z-index:3000;';

            const content = document.createElement('div');
            content.className = 'modal-content';
            content.style.cssText = 'position:absolute; top:50%; left:50%; transform:translate(-50%, -50%); background:var(--bg-white); padding:20px; border-radius:8px; width:400px; max-width:90%;';

            content.innerHTML = `
                <h3 style="margin-top:0; color:var(--primary-color);">Nova Ranking</h3>
                <div style="margin-bottom:15px;">
                    <strong>Object:</strong> <span id="nova-rank-object"></span>
                </div>
                <div style="margin-bottom:15px;">
                    <strong>Rank:</strong> <span id="nova-rank-rank"></span>
                </div>
                <div style="margin-bottom:15px;">
                    <strong>Reason:</strong> <p id="nova-rank-reason" style="margin:5px 0 0 0; line-height:1.4;"></p>
                </div>
                <div style="margin-bottom:15px;">
                    <strong>${window.t ? window.t('recommended_rigs') : 'Recommended Rigs'}:</strong>
                    <div id="nova-rank-rigs" style="margin:5px 0 0 0; line-height:1.4;"></div>
                </div>
                <div style="display:flex; gap:10px; justify-content:flex-end; margin-top:20px;">
                    <button id="nova-rank-close-btn" style="background:var(--accent-gray-medium); color:white; border:none; padding:6px 12px; border-radius:4px; cursor:pointer;">Close</button>
                </div>
            `;

            modal.appendChild(content);
            document.body.appendChild(modal);

            // Add click handler for close button
            document.getElementById('nova-rank-close-btn').addEventListener('click', function() {
                modal.style.display = 'none';
            });

            // Close on backdrop click
            modal.addEventListener('click', function(e) {
                if (e.target === modal) {
                    modal.style.display = 'none';
                }
            });
        }

        // Populate modal content
        document.getElementById('nova-rank-object').textContent = objectName;
        document.getElementById('nova-rank-rank').textContent = '#' + rank;
        const shortReason = reason.length > 200 ? reason.substring(0, 200) + '…' : reason;
        document.getElementById('nova-rank-reason').textContent = shortReason;

        // Display rigs as numbered list
        const rigsElement = document.getElementById('nova-rank-rigs');
        if (rigs.length > 0) {
            rigsElement.innerHTML = rigs.map((rig, index) => {
                return `${index + 1}. ${rig}`;
            }).join('<br>');
        } else {
            rigsElement.textContent = 'N/A';
        }

        // Show modal
        modal.style.display = 'block';
    }

    /**
     * Handle Ask Nova button click
     */
    async function askNova() {
        console.log('[Nova] askNova started, controller state:',
            askNovaController ? 'exists' : 'null',
            'signal aborted:', askNovaController?.signal?.aborted)
        console.log('askNova triggered');
        const askNovaBtn = document.getElementById('ask-nova-btn');
        const errorDiv = document.getElementById('ask-nova-error');

        if (!askNovaBtn) return;

        // Reset button state
        errorDiv.style.display = 'none';

        // Get current data and filters
        const allData = window.latestDSOData || [];
        if (allData.length === 0) {
            errorDiv.textContent = window.t ? window.t('no_data_available') : 'No data available';
            errorDiv.style.display = 'block';
            return;
        }

        // Pre-filter by imaging criteria
        const imagingCriteria = window.NOVA_INDEX.imagingCriteria || {};
        const minObservableMinutes = imagingCriteria.min_observable_minutes || 60;
        const minMaxAltitude = imagingCriteria.min_max_altitude || 30;

        // Filter objects that meet the imaging criteria
        const filteredObjects = allData.filter(obj => {
            const obsDuration = parseFloat(obj['Observable Duration (min)'] || 0);
            const maxAlt = parseFloat(obj['Max Altitude (°)'] || 0);

            // Check if object meets minimum criteria
            return obsDuration >= minObservableMinutes && maxAlt >= minMaxAltitude;
        });

        if (filteredObjects.length === 0) {
            errorDiv.textContent = window.t ? window.t('no_targets_found') : 'No targets found matching your criteria.';
            errorDiv.style.display = 'block';
            return;
        }

        // Get current location and sim date
        const locationName = sessionStorage.getItem('selectedLocation') || document.getElementById('location-select')?.value;
        const simModeOn = document.getElementById('sim-mode-toggle')?.checked;
        const simDateVal = document.getElementById('sim-date-input')?.value;
        const effectiveDate = simModeOn && simDateVal ? simDateVal : null;

        if (!locationName) {
            errorDiv.textContent = 'Please select a location first.';
            errorDiv.style.display = 'block';
            return;
        }

        // Set loading state with alternating messages
        askNovaBtn.disabled = true;

        // Build message sequence with location name
        const novaMessages = [
            window.t('consulting_charts'),
            window.t('checking_rigs'),
            window.t('scanning_cosmos'),
            window.t('almost_there')
        ];

        // Start message animation
        let msgIndex = 0;
        const msgInterval = setInterval(() => {
            msgIndex = (msgIndex + 1) % novaMessages.length;
            askNovaBtn.innerHTML = `
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" style="flex-shrink:0">
                    <path d="M8 1L9.5 6H14.5L10.5 9L12 14L8 11L4 14L5.5 9L1.5 6H6.5L8 1Z" fill="currentColor"/>
                </svg>
                ${novaMessages[msgIndex]}
            `;
        }, 2500);

        // Set initial message
        askNovaBtn.innerHTML = `
            <svg width="13" height="13" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" style="flex-shrink:0">
                <path d="M8 1L9.5 6H14.5L10.5 9L12 14L8 11L4 14L5.5 9L1.5 6H6.5L8 1Z" fill="currentColor"/>
            </svg>
            ${novaMessages[0]}
        `;

        try {
            console.log('[Nova] Sending to AI:', filteredObjects.length, 'objects');

            // Abort any previous Ask Nova request
            if (askNovaController) {
                askNovaController.abort();
            }
            if (askNovaTimeoutId) {
                clearTimeout(askNovaTimeoutId);
            }

            // AbortController for timeout
            askNovaController = new AbortController();
            askNovaTimeoutId = setTimeout(() => askNovaController.abort(), 180000);

            // Call the API
            console.log('[Nova] About to fetch, signal aborted:',
                askNovaController.signal.aborted)
            const response = await fetch('/api/ai/best_objects', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    object_list: filteredObjects,
                    location_name: locationName,
                    sim_date: effectiveDate
                }),
                signal: askNovaController.signal
            });

            console.log('[Nova] Fetch returned, status:', response.status)
            clearTimeout(askNovaTimeoutId);

            if (response.status === 404) {
                throw new Error('Nova AI endpoint not available. Check AI_API_KEY configuration.');
            }

            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.error || 'API request failed');
            }

            const result = await response.json();
            console.log('[Nova] AI response received');
            const rankedObjects = result.ranked_objects || [];

            // Save original data before applying Nova ranking (for resetRanking)
            originalTableData = [...(window.latestDSOData || [])];

            // Populate module-level novaRankMap (case-insensitive, stored as uppercase)
            novaRankMap = {};
            rankedObjects.forEach(ranked => {
                const objectNameUpper = (ranked.Object || '').trim().toUpperCase();
                novaRankMap[objectNameUpper] = {
                    rank: parseInt(ranked.rank, 10), // Ensure rank is stored as number
                    reason: ranked.reason || '',
                    recommendedRigs: Array.isArray(ranked.recommended_rigs) ? ranked.recommended_rigs : []
                };
            });

            // Persist novaRankMap to sessionStorage for navigation persistence
            sessionStorage.setItem('novaRankMap', JSON.stringify(novaRankMap));
            sessionStorage.setItem('novaRankMapSize', originalTableData.length);

            // Re-render with ranked data (data-level sorting, not DOM manipulation)
            const sortedData = applyNovaRankSorting(window.latestDSOData);
            renderRows(sortedData);

            // Show Remove Filters button and mark Ask Nova button as active
            updateRemoveFiltersButtonVisibility();
            askNovaBtn.classList.add('active');
            document.getElementById('data-table').classList.add('nova-active');

        } catch (error) {
            console.error('Error asking Nova:', error);
            if (error.name === 'AbortError') {
                errorDiv.textContent = 'Nova took too long to respond. Please try again.';
            } else {
                errorDiv.textContent = window.t ? window.t('error_ask_nova') : error.message;
            }
            errorDiv.style.display = 'block';
        } finally {
            // Stop message animation
            clearInterval(msgInterval);

            // Reset button state (remove loading, restore to default or active state)
            askNovaBtn.disabled = false;
            askNovaController = null;
            askNovaTimeoutId = null;
            // Use innerHTML to preserve the SVG icon
            const activeClass = askNovaBtn.classList.contains('active') ? ' active' : '';
            askNovaBtn.innerHTML = `
                <svg width="13" height="13" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg" style="flex-shrink:0">
                    <path d="M8 1L9.5 6H14.5L10.5 9L12 14L8 11L4 14L5.5 9L1.5 6H6.5L8 1Z" fill="currentColor"/>
                </svg>
                ${window.t ? window.t('ask_nova') : 'Ask Nova'}
            `;
            // Restore the active class if it was added (the innerHTML replacement removes all attributes)
            if (activeClass) {
                askNovaBtn.classList.add('active');
            }
        }
    }

    /**
     * Re-sort table rows based on Nova's ranking
     * @param {Object} rankMap - Map of object names to rank data
     */
    function reSortTableByRank(rankMap) {
        const tbody = document.getElementById('data-body');
        const rows = Array.from(tbody.querySelectorAll('tr'));

        // Separate ranked and unranked rows
        const rankedRows = [];
        const unrankedRows = [];

        rows.forEach(row => {
            // Find object name in first cell
            const objectCell = row.querySelector('td[data-column-key="Object"]');
            if (!objectCell) return;

            // Use case-insensitive matching (uppercase for lookup)
            const objectName = objectCell.textContent.trim();
            const objectNameUpper = objectName.toUpperCase();
            const rankData = rankMap[objectNameUpper];

            if (rankData) {
                rankedRows.push({
                    row: row,
                    rank: rankData.rank,
                    reason: rankData.reason,
                    recommendedRig: rankData.recommendedRig
                });
            } else {
                unrankedRows.push(row);
            }
        });

        // Sort ranked rows by rank
        rankedRows.sort((a, b) => a.rank - b.rank);

        // Clear table and re-append in new order
        tbody.innerHTML = '';

        // Add ranked rows with badges
        rankedRows.forEach(({row, rank, reason, recommendedRig}) => {
            // Find the Object cell and add badge
            const objectCell = row.querySelector('td[data-column-key="Object"]');
            if (objectCell) {
                // Remove any existing badge
                const existingBadge = objectCell.querySelector('.nova-rank-badge');
                if (existingBadge) existingBadge.remove();

                // Create rank badge
                const badge = document.createElement('span');
                badge.className = 'nova-rank-badge';
                badge.textContent = '#' + rank;

                // Add tooltip with reason
                badge.title = reason;

                // Insert badge before the object name
                objectCell.insertBefore(badge, objectCell.firstChild);
            }

            tbody.appendChild(row);
        });

        // Add unranked rows at the bottom
        unrankedRows.forEach(row => {
            tbody.appendChild(row);
        });
    }

    /**
     * Reset table to original sorting and remove rank badges
     */
    function resetRanking() {
        // Clear Object search filter
        const objectFilterInput = document.querySelector('#data-table .filter-row th[data-column-key="Object"] input');
        if (objectFilterInput) {
            objectFilterInput.value = '';
            localStorage.removeItem("dso_filter_col_key_Object");
        }

        // Clear module-level novaRankMap
        novaRankMap = {};

        // Clear sessionStorage persistence
        sessionStorage.removeItem('novaRankMap');
        sessionStorage.removeItem('novaRankMapSize');

        // Re-render with original full dataset (if available)
        if (originalTableData && originalTableData.length > 0) {
            renderRows(originalTableData);
            filterTable();
        } else if (window.latestDSOData && window.latestDSOData.length > 0) {
            renderRows(window.latestDSOData);
            filterTable();
        }

        // Update button visibility
        updateRemoveFiltersButtonVisibility();

        // Remove active state from Ask Nova button
        const askNovaBtn = document.getElementById('ask-nova-btn');
        if (askNovaBtn) {
            askNovaBtn.classList.remove('active');
        }
        document.getElementById('data-table').classList.remove('nova-active');

        // Clear error
        const errorDiv = document.getElementById('ask-nova-error');
        if (errorDiv) {
            errorDiv.style.display = 'none';
        }
    }


    // --- Helper: Render All (Replaces Table) ---
    function renderRows(data) {
        const tbody = document.getElementById("data-body");
        window.latestDSOData = data; // <--- CAPTURE DATA HERE
        tbody.innerHTML = '';
            appendRows(data);
    
            // NOTE: Auto-refresh for Inspiration tab removed to prevent jarring shuffles.
            // The user must click "Refresh" to see new suggestions.
        }

    // --- Helper: Append Rows (Adds to existing) ---
    function appendRows(data) {
        const tbody = document.getElementById("data-body");
        const altitudeThreshold = window.NOVA_INDEX.altitudeThreshold;
        const columnOrder = [
            'Object', 'Common Name', 'Altitude Current', 'Azimuth Current', 'Trend', 'Altitude 11PM',
            'Azimuth 11PM', 'Transit Time', 'Observable Duration (min)', 'Max Altitude (°)',
            'Angular Separation (°)', 'Constellation', 'Type', 'Magnitude', 'Size', 'SB',
            'Best Month', 'Max Altitude'
        ];
    
        data.forEach(objectData => {
            if (!objectData) return;
    
            const sanitizedId = String(objectData.Object || 'unknown').replace(/\s+/g, '-');
            const row = document.createElement('tr');
            row.id = `row-${sanitizedId}`;
            row.className = 'clickable-row';
    
            // Store impossible status for sorting logic
            row.dataset.impossible = objectData.is_geometrically_impossible ? 'true' : 'false';
    
            if (objectData.error === true || (objectData['Common Name'] && String(objectData['Common Name']).startsWith("Error:"))) {
                row.style.backgroundColor = getDangerBgColor();
                row.style.color = getDangerTextColor();
                row.style.cursor = 'default';
            } else if (objectData.is_geometrically_impossible) {
                // Apply "Greyed Out" styling
                row.classList.add('geometrically-impossible');
                row.title = "Object never rises above your altitude threshold.";
                row.onclick = () => { showGraph(objectData.Object); }; // Still allow click to verify graph
            } else {
                const ap = objectData.ActiveProject;
                if ((ap === true) || (ap === 1) || (ap === '1') || (ap === 'true')) {
                    row.classList.add('active-project-row');
                }
                row.onclick = () => { showGraph(objectData.Object); };
            }

            // Nova Rank cell — always created, CSS controls visibility
            const novaRankTd = document.createElement('td');
            novaRankTd.dataset.columnKey = 'Nova Rank';
            novaRankTd.className = 'col-nova-rank';
            novaRankTd.style.textAlign = 'center';
            const objectNameUpper = String(objectData.Object || '').trim().toUpperCase();
            const rankData = novaRankMap[objectNameUpper];
            novaRankTd.dataset.rawValue = rankData ? rankData.rank : 9999;
            novaRankTd.textContent = rankData ? rankData.rank : '';

            if (rankData) {
                novaRankTd.style.cursor = 'pointer';
                novaRankTd.addEventListener('click', function(e) {
                    e.stopPropagation();
                    showNovaRankModal(
                        objectData.Object,
                        rankData.rank,
                        rankData.reason,
                        rankData.recommendedRigs
                    );
                });
            }

            row.appendChild(novaRankTd);

            columnOrder.forEach(columnKey => {
                const config = columnConfig[columnKey];
                if (!config) return;
    
                const td = document.createElement('td');
                td.dataset.columnKey = columnKey;
                td.style.textAlign = (columnKey === 'Object' || columnKey === 'Common Name') ? 'left' : 'center';
    
                const rawValue = objectData[config.dataKey];
                const displayValue = (rawValue === null || rawValue === undefined || String(rawValue).trim() === '')
                                     ? 'N/A'
                                     : (config.format ? config.format(rawValue) : rawValue);
    
                if (columnKey === 'Common Name') {
                    // Create flex container to hold text and icon side-by-side
                    const container = document.createElement('div');
                    container.style.display = 'flex';
                    container.style.alignItems = 'center';
                    container.style.justifyContent = 'space-between';
                    container.style.width = '100%';
    
                    // Text Span (Handles ellipsis)
                    const spanText = document.createElement('span');
                    spanText.textContent = displayValue;
                    spanText.style.flex = '1';
                    spanText.style.whiteSpace = 'nowrap';
                    spanText.style.overflow = 'hidden';
                    spanText.style.textOverflow = 'ellipsis';
                    container.appendChild(spanText);
    
                    // Check for available inspiration content (Custom Image OR Desc)
                    const hasCustomContent = (objectData.image_url && objectData.image_url.trim() !== "") || (objectData.description_text && objectData.description_text.trim() !== "");
    
                    // Only show the icon if there is CUSTOM content (ignore coordinates/fallbacks for the table view)
                    if (hasCustomContent) {
                        // Inspiration Icon
                        const icon = document.createElement('span');
                        icon.innerHTML = '&#9432;'; // Circled i
                    icon.style.cursor = 'pointer';
                    icon.style.marginLeft = '8px';
                    icon.style.color = getPrimaryColor();
                    icon.style.fontSize = '1.2em';
                    icon.style.lineHeight = '1';
                    icon.title = "View Inspiration";
    
                    // Click Handler: Stop propagation to prevent opening the graph
                    icon.onclick = function(e) {
                        e.stopPropagation();
    
                        // 1. Resolve Image (Custom > DSS2 Fallback)
                        let finalImg = objectData.image_url;
                        let finalSource = objectData.image_credit || "Catalog";
                        let finalLink = objectData.image_source_link || "";
    
                        // If no custom image, try calculating DSS2 URL using helper from _inspiration_section.html
                        if (!finalImg && typeof getAladinFallbackUrl === 'function') {
                            const raDeg = (parseFloat(objectData['RA (hours)']) || 0) * 15;
                            const decDeg = parseFloat(objectData['DEC (degrees)']) || 0;
                            if (objectData['RA (hours)'] != null) {
                                finalImg = getAladinFallbackUrl(raDeg, decDeg, objectData.Size);
                                finalSource = "DSS2";
                            }
                        }
    
                        // 2. Resolve Description Text
                        let finalText = objectData.description_text;
                        if (!finalText) {
                             finalText = `Type: ${objectData.Type || 'DSO'}. Located in ${objectData.Constellation || 'unknown'}.`;
                        }
    
                        // 3. Open Modal
                        if (typeof openInspirationModal === 'function') {
                            openInspirationModal(objectData, finalImg, finalText, finalSource, finalLink);
                            // Graph load is now handled automatically by the global hook in window.onload
                        } else {
                            console.warn("Inspiration modal function not found.");
                        }
                    };
    
                    container.appendChild(icon);
                } // End if (hasCustomContent || hasCoordinates)
    
                td.innerHTML = ''; // Clear any existing text
                td.appendChild(container);
            } else if (columnKey === 'Trend') {
                // Trend column - wrap in span with appropriate class for coloring
                const trendSpan = document.createElement('span');
                if (rawValue === '↑') {
                    trendSpan.className = 'trend-up';
                } else if (rawValue === '↓') {
                    trendSpan.className = 'trend-down';
                }
                trendSpan.textContent = displayValue;
                td.appendChild(trendSpan);
            } else {
                    td.textContent = displayValue;
                }
    
                // Add tooltip for both Object and Common Name so truncated text can be read
                if ((columnKey === 'Common Name' || columnKey === 'Object') && rawValue) {
                    td.setAttribute('title', String(rawValue));
                }
    
                // Immediate Visibility Check (Prevents flash of hidden columns during loading)
                const isVisible = (config.type === 'always-visible' || config.type === activeTab);
                td.style.display = isVisible ? 'table-cell' : 'none';
    
                // Highlights
                _applyRowHighlights(td, columnKey, rawValue, objectData, altitudeThreshold);
    
                // Sort helpers
                const numericSortKeys = ['Altitude Current', 'Azimuth Current', 'Altitude 11PM', 'Azimuth 11PM',
                                         'Observable Duration (min)', 'Max Altitude (°)', 'Angular Separation (°)',
                                         'Magnitude', 'Size', 'SB', 'Max Altitude', 'Nova Rank'];
    
                if (numericSortKeys.includes(columnKey)) {
                    if (rawValue === 'N/A' || rawValue == null) td.dataset.rawValue = 'N/A';
                    else if (!isNaN(parseFloat(rawValue))) td.dataset.rawValue = parseFloat(rawValue);
                    else td.dataset.rawValue = rawValue;
                } else if (rawValue === 'N/A' || rawValue == null) {
                    td.dataset.rawValue = 'N/A';
                }
    
                row.appendChild(td);
            });
            tbody.appendChild(row);
        });
    }
    
    function finalizeFetch() {
        const tbody = document.getElementById("data-body");
        const loadingDiv = document.getElementById("table-loading");
        _hideFetchLoader(loadingDiv);
        applyDsoColumnVisibility();
        if (Object.keys(novaRankMap).length === 0) {
            sortTable(currentSort.columnKey, false);
        }

        // 1. Apply filters (calculates window.currentFilteredData)
        filterTable();

        tbody.dataset.loading = 'false';
        fetchSunEvents();

        // 2. NOW it is safe to update the Inspiration grid with filtered data
        if (activeTab === 'inspiration' && typeof renderInspirationGrid === 'function') {
            renderInspirationGrid();
        }

        // REMOVED: Location change check was causing duplicate fetch on initial page load
        // The fetchLocations() call in window.onload sets sessionStorage before fetchData() runs,
        // and location-select change event handles user-triggered changes. No need to re-check here.
    }

    function sortOutlookTable(columnKey, toggle = true) {
        if (toggle) {
            if (currentOutlookSort.columnKey === columnKey) {
                currentOutlookSort.ascending = !currentOutlookSort.ascending;
            } else {
                currentOutlookSort.columnKey = columnKey;
                currentOutlookSort.ascending = (columnKey === 'date');
            }
        }
    
        const config = outlookColumnConfig[columnKey];
        if (!config) return;
    
        allOutlookOpportunities.sort((a, b) => {
            let valA = a[config.dataKey];
            let valB = b[config.dataKey];
    
            if (config.dataKey === 'date') {
                 return currentOutlookSort.ascending ? new Date(valA) - new Date(valB) : new Date(valB) - new Date(valA);
            }
    
            if (config.numeric) {
                valA = parseFloat(valA) || 0;
                valB = parseFloat(valB) || 0;
                return currentOutlookSort.ascending ? valA - valB : valB - valA;
            } else {
                return currentOutlookSort.ascending ? String(valA).localeCompare(String(valB)) : String(valB).localeCompare(String(valA));
            }
        });
    
        document.querySelectorAll('#outlook-table th .sort-indicator').forEach(span => span.innerHTML = '');
        const activeTh = document.querySelector(`#outlook-table th[data-outlook-column-key="${columnKey}"] .sort-indicator`);
        if (activeTh) activeTh.innerHTML = currentOutlookSort.ascending ? '▲' : '▼';
    
        renderOutlookTable();
    }
    
    function filterOutlookTable() {
        renderOutlookTable(); // The render function will handle filtering
    }
    
    function sortTable(columnKey, toggle = true) { // DSO Table Sort
        const table = document.getElementById("data-table");
        if (!table) return;
        let sortOrder;
        if (toggle) {
            if (currentSort.columnKey === columnKey) { currentSort.ascending = !currentSort.ascending; }
            else { currentSort.columnKey = columnKey; currentSort.ascending = true; }
            localStorage.setItem("dso_sortOrder", currentSort.ascending ? "asc" : "desc");
            localStorage.setItem("dso_sortColumnKey", columnKey);
        } else {
            const storedSortOrder = localStorage.getItem("dso_sortOrder");
            const storedSortColumnKey = localStorage.getItem("dso_sortColumnKey");
            if (storedSortColumnKey) {
                currentSort.columnKey = storedSortColumnKey;
                currentSort.ascending = (storedSortOrder === "asc");
            }
        }
        sortOrder = currentSort.ascending ? "asc" : "desc";
        table.setAttribute("data-sort-order", sortOrder);
        const tbody = document.getElementById("data-body");
        if (!tbody) return;
        const rows = Array.from(tbody.getElementsByTagName("tr"));
        const config = columnConfig[currentSort.columnKey];
    
        rows.sort((a, b) => {
                // Priority Sort: Push "Geometrically Impossible" (greyed out) rows to the bottom always
                const impA = a.dataset.impossible === 'true';
                const impB = b.dataset.impossible === 'true';
                if (impA !== impB) return impA ? 1 : -1;
    
                const cellA_element = a.querySelector(`td[data-column-key="${currentSort.columnKey}"]`);
                const cellB_element = b.querySelector(`td[data-column-key="${currentSort.columnKey}"]`);
                if (!cellA_element || !cellB_element) return 0;
    
                // FIX: Use textContent for sorting hidden tables
                let valA_str = cellA_element.dataset.rawValue !== undefined ? cellA_element.dataset.rawValue : cellA_element.textContent.trim();
                let valB_str = cellB_element.dataset.rawValue !== undefined ? cellB_element.dataset.rawValue : cellB_element.textContent.trim();
    
                const isNA_A = valA_str === 'N/A' || valA_str === ''; const isNA_B = valB_str === 'N/A' || valB_str === '';
                if (isNA_A && isNA_B) return 0; if (isNA_A) return currentSort.ascending ? 1 : -1; if (isNA_B) return currentSort.ascending ? -1 : 1;
                let valA = valA_str; let valB = valB_str;
                // --- MODIFIED TO ADD NEW COLUMN ---
                const numericSortKeys = [
                    'Altitude Current', 'Azimuth Current', 'Altitude 11PM', 'Azimuth 11PM',
                    'Observable Duration (min)', 'Max Altitude (°)', 'Angular Separation (°)',
                    'Magnitude', 'Size', 'SB', 'Max Altitude', 'Nova Rank'
                ];
                // --- END MODIFICATION ---
                if (config && numericSortKeys.includes(currentSort.columnKey)) { valA = parseFloat(valA_str); valB = parseFloat(valB_str); }
                else if (currentSort.columnKey === 'Transit Time' && /^\d{1,2}:\d{2}$/.test(valA_str) && /^\d{1,2}:\d{2}$/.test(valB_str)) { valA = parseTimeToMinutes(valA_str); valB = parseTimeToMinutes(valB_str); }
                if (typeof valA === 'number' && typeof valB === 'number') { if (isNaN(valA) && isNaN(valB)) return 0; if (isNaN(valA)) return currentSort.ascending ? 1 : -1; if (isNaN(valB)) return currentSort.ascending ? -1 : 1; return currentSort.ascending ? valA - valB : valB - valA; }
                return currentSort.ascending ? String(valA).localeCompare(String(valB)) : String(valB).localeCompare(String(valA));
            });
            rows.forEach(row => tbody.appendChild(row));
            updateSortIndicators();
        }
    
        function updateSortIndicators() { // DSO Table
            document.querySelectorAll('#data-table > thead > tr:not(.filter-row) > th .sort-indicator').forEach(span => span.innerHTML = '');
            const activeTh = document.querySelector(`#data-table > thead > tr:not(.filter-row) > th[data-column-key="${currentSort.columnKey}"]`);
            if (activeTh) { const indicator = activeTh.querySelector('.sort-indicator'); if (indicator) indicator.innerHTML = currentSort.ascending ? '▲' : '▼'; }
        }
    
        function filterTable() { // DSO Table
          // --- FIX: Initialize global filter flag ---
          window.isListFiltered = false;
    
          const tbody = document.getElementById("data-body");
          if (!tbody) return;
          const rows = tbody.getElementsByTagName("tr");
          const activeFilters = {};
          for (const columnKey in columnConfig) {
            if (columnConfig.hasOwnProperty(columnKey) && columnConfig[columnKey].filterable) {
              const inputElement = document.querySelector(`#data-table .filter-row th[data-column-key="${columnKey}"] input`);
              if (inputElement && inputElement.value.trim() !== '') {
                activeFilters[columnKey] = inputElement.value.trim().toLowerCase();
              }
            }
          }
          for (let i = 0; i < rows.length; i++) {
            let showRow = true;
            for (const columnKeyInFilter in activeFilters) {
                const filterValue = activeFilters[columnKeyInFilter]; const config = columnConfig[columnKeyInFilter];
                if (!config) continue;
                const cellElement = rows[i].querySelector(`td[data-column-key="${columnKeyInFilter}"]`);
                if (!cellElement) { showRow = false; break; }
    
                // FIX: Use textContent instead of innerText.
                // innerText returns "" if the table is hidden (e.g. Inspiration Tab), causing filters to fail.
                let cellText = (cellElement.dataset.rawValue || cellElement.textContent).trim().toLowerCase();
    
                if (filterValue === "n/a" || filterValue === "na") { if (cellText !== "n/a") { showRow = false; break; } continue; }
                if (config.dataKey === 'Type') {
                    const filterTypes = filterValue.split(/[\s,]+/).filter(t => t.length > 0);
    
                    if (filterTypes.length > 0) {
                        let typeMatch = false;
    
                        // Split the cell text into specific tokens (words) to prevent "G" matching "GlC"
                        // cellText is already lowercased at the start of the loop
                        const cellTokens = cellText.split(/[\s,]+/).filter(t => t.length > 0);
    
                        for (const typeTerm of filterTypes) {
                            // Check if the search term exists exactly in the list of cell tokens
                            if (cellTokens.includes(typeTerm)) {
                                typeMatch = true;
                                break;
                            }
                        }
                        if (!typeMatch) { showRow = false; break; }
                    }
                } else {
                    const numericFilterKeys = [
                        'Altitude Current', 'Azimuth Current', 'Altitude 11PM', 'Azimuth 11PM',
                        'Observable Duration (min)', 'Max Altitude (°)', 'Angular Separation (°)',
                        'Magnitude', 'Size', 'SB', 'Max Altitude'
                    ];
    
                    // --- THIS IS THE FIX ---
                    // We must check if the columnKeyInFilter is in the array,
                    // NOT the config.dataKey.
                    if (config && numericFilterKeys.includes(columnKeyInFilter)) {
                    // --- END OF FIX ---
                        if (cellText === "n/a") { showRow = false; break; }
                        const cellNumber = parseFloat(cellText.replace(/[^0-9.\-]/g, ""));
                        if (isNaN(cellNumber)) {
                            // If we can't parse a number, fall back to substring match
                            if (cellText.indexOf(filterValue) === -1) { showRow = false; break; }
                            continue;
                        }
                        if (!matchesNumericFilter(cellNumber, filterValue)) { showRow = false; break; }
                    } else {
                        // --- NEW LOGIC for comma-separated filters ---
                        if (columnKeyInFilter === 'Best Month') {
                            const filterTerms = filterValue.split(/[\s,]+/).filter(t => t.length > 0);
                            if (filterTerms.length > 0) {
                                // Check if the cellText (e.g., "dec") matches ANY of the filter terms (e.g., "dec", "jan")
                                const matchFound = filterTerms.some(term => cellText.includes(term));
                                if (!matchFound) {
                                    showRow = false;
                                    break;
                                }
                            }
                        // --- END NEW LOGIC ---
                        } else if (filterValue.startsWith("!")) {
                            if (cellText.includes(filterValue.substring(1))) { showRow = false; break; }
                        } else {
                            if (!cellText.includes(filterValue)) { showRow = false; break; }
                        }
                    }
                }
            }
    
            // --- HIDE INVISIBLE LOGIC ---
            // If the row matches filters (showRow is true), AND the user wants to hide invisible objects,
            // AND no specific filters are active (default view), then force hide it.
            // If the user HAS typed a filter (activeFilters not empty), we let it show (override hiding).
            if (showRow && HIDE_INVISIBLE_PREF) {
                const isImpossible = rows[i].dataset.impossible === 'true';
                const filtersActive = Object.keys(activeFilters).length > 0;
    
                if (isImpossible && !filtersActive) {
                    showRow = false;
                }
            }
    
            // --- NEW: Global Active Only Logic ---
            const globalActiveToggle = document.getElementById('global-active-toggle');
            if (showRow && globalActiveToggle && globalActiveToggle.checked) {
                // Rows are marked with 'active-project-row' in appendRows() if they are active
                if (!rows[i].classList.contains('active-project-row')) {
                    showRow = false;
                }
            }
            // -------------------------------------
    
            rows[i].style.display = showRow ? "" : "none";
          }
    
          // --- EXPOSE FILTERED DATA FOR INSPIRATION TAB ---
    
          // 1. Set Flag: Filters are active if activeFilters has keys
          window.isListFiltered = (Object.keys(activeFilters).length > 0);
    
          // 2. Reconstruct visible list robustly
          if (window.latestDSOData && window.latestDSOData.length > 0) {
              const filteredList = [];
              // Create a map for O(1) lookup of source data by Object Name
              const dataMap = new Map(window.latestDSOData.map(item => [String(item.Object), item]));
    
              for (let i = 0; i < rows.length; i++) {
                  // Check the style we *just set* in the loop above.
                  // This is valid even if the parent container is hidden (display: none).
                  if (rows[i].style.display !== "none") {
                      const nameCell = rows[i].querySelector('td[data-column-key="Object"]');
                      if (nameCell) {
                          const objName = nameCell.textContent.trim();
                          const dataObj = dataMap.get(objName);
                          if (dataObj) {
                              filteredList.push(dataObj);
                          }
                      }
                  }
              }
              window.currentFilteredData = filteredList;
          } else {
              window.currentFilteredData = [];
          }
          // ------------------------------------------------
    
          updateRemoveFiltersButtonVisibility();
          if (outlookDataLoaded) { renderOutlookTable(); }
        }
    
        function saveFilter(inputElement, columnKey, tableType = 'dso') {
          localStorage.setItem(tableType + "_filter_col_key_" + columnKey, inputElement.value);
        }
    
        function showGraph(objectName, dateStr = null, targetTab = 'chart') {
            let url = '/graph_dashboard/' + encodeURIComponent(objectName);
            const params = new URLSearchParams();
            const currentSelectedLocation = sessionStorage.getItem('selectedLocation');
            if (currentSelectedLocation) {
                params.set('location', currentSelectedLocation); // Add location to URL parameters
            }
    
            const simToggle = document.getElementById('sim-mode-toggle');
            const simInput = document.getElementById('sim-date-input');
            if (dateStr) { // A date is explicitly passed (e.g., from Outlook)
                const parts = dateStr.split('-');
                if (parts.length === 3) {
                    params.set('year', parts[0]);
                    params.set('month', parts[1]);
                    params.set('day', parts[2]);
                }
            } else if (simToggle && simToggle.checked && simInput && simInput.value) { // No date passed, but sim mode is on
                const parts = simInput.value.split('-');
                if (parts.length === 3) {
                    params.set('year', parts[0]);
                    params.set('month', parts[1]);
                    params.set('day', parts[2]);
                }
            }
    
            // This 'if' block is the key part of the logic
            if (targetTab) {
                params.set('tab', targetTab);
            }
    
            const queryString = params.toString();
            if (queryString) {
                url += '?' + queryString;
            }
    
            window.location.href = url;
        }
    
    function fetchLocations() {
            return fetch('/get_locations')
            .then(response => response.json())
            .then(data => {
                let locationSelect = document.getElementById('location-select');
                if (!locationSelect) return;
                locationSelect.innerHTML = ''; // Clear existing options
    
                // --- ADD: Determine initial location ---
                let initialLocation = sessionStorage.getItem('selectedLocation');
                // If nothing in session storage OR if the stored location is no longer valid/active, use the default from backend
                if (!initialLocation || !data.locations.includes(initialLocation)) {
                    initialLocation = data.selected; // 'selected' is the default from backend
                    if (initialLocation) {
                        sessionStorage.setItem('selectedLocation', initialLocation);
                    }
                }
                // --- END ADD ---
    
                data.locations.forEach(location => {
                    let option = document.createElement('option');
                    option.value = location;
                    option.textContent = location;
                    // --- MODIFY: Select based on initialLocation ---
                    if (location === initialLocation) {
                        option.selected = true;
                    }
                    // --- END MODIFY ---
                    locationSelect.appendChild(option);
                });
    
                // Ensure dropdown matches the validated location
                 if (locationSelect.value !== initialLocation && initialLocation) {
                     locationSelect.value = initialLocation;
                 }

                 // Return validated location for chaining
                 return initialLocation;
            })
            .catch(error => {
                console.error('Error fetching locations:', error);
                return null;
            });
        }
    
    function setLocation() {
        // Get the table body to check its loading status
        const tbody = document.getElementById("data-body");

        // Get the newly selected location
        const selectedLocation = document.getElementById('location-select').value;

        // Always store the user's *latest* choice in sessionStorage.
        console.log(`Location select changed to: ${selectedLocation}`);
        sessionStorage.setItem('selectedLocation', selectedLocation);
        outlookDataLoaded = false; // Reset outlook flag for the new location
        if (typeof resetHeatmapState === 'function') resetHeatmapState();

        // Persist the location server-side so it's remembered across page navigations
        fetch('/set_location', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({location: selectedLocation})
        }).then(response => response.json())
          .then(data => {
              if (data.status !== 'success') {
                  console.warn('Failed to persist location:', data.message);
              }
          }).catch(error => console.error('Error persisting location:', error));

        // Helper elements
        const loadingDiv = document.getElementById('table-loading');
        const loadingMessage = document.getElementById("loading-message");
        const progressBar = document.getElementById("loading-progress-bar");

        // --- (Removed blocking check) Always process the latest user request ---
        // fetchData handles aborting previous requests automatically via AbortController.
        console.log(`Triggering fetch for new location: ${selectedLocation}`);

        // Clear existing data immediately
        document.getElementById('data-body').innerHTML = '';
        document.getElementById('journal-data-body').innerHTML = '';
        document.getElementById('outlook-body').innerHTML = '';

        // Show loading indicator WITHOUT destroying the progress bar structure
        if (loadingDiv) {
            loadingDiv.style.display = "block";
            if (loadingMessage) loadingMessage.textContent = window.t('updating_location');
            if (progressBar) progressBar.style.width = "0%";
        }

        // Trigger the data fetches for the new location
        fetchData();

        if (activeTab === 'journal') {
           populateJournalTable();
        } else if (activeTab === 'outlook') {
           fetchOutlookData();
        } else if (activeTab === 'heatmap') {
           fetchAndRenderHeatmap();
        }
    }
    
        function fetchSunEvents() {
            const currentSelectedLocation = sessionStorage.getItem('selectedLocation');
            const simModeOn = document.getElementById('sim-mode-toggle')?.checked;
            const simDateVal = document.getElementById('sim-date-input')?.value;
            const effectiveDate = simModeOn && simDateVal ? simDateVal : null;
            let url = `/sun_events?location=${encodeURIComponent(currentSelectedLocation || '')}`;
            if (effectiveDate) {
                url += `&sim_date=${effectiveDate}`;
            }
            fetch(url)
            .then(response => response.json())
            .then(data => {
              document.getElementById('dawn').textContent = data.astronomical_dawn;
              document.getElementById('dusk').textContent = data.astronomical_dusk;
              const dateEl = document.getElementById('date');
              const timeEl = document.getElementById('time');
              const phaseEl = document.getElementById('phase');
              if(dateEl && data.date) dateEl.textContent = formatDateISOtoEuropean(data.date);
              if(timeEl && data.time) timeEl.textContent = data.time;
              if(phaseEl && data.phase !== undefined) phaseEl.textContent = `${data.phase}%`;
            })
            .catch(error => { console.error('Error fetching sun events:', error);});
        }
    
        // ========================================================================
        // Journal Table Functions
        // ========================================================================

        /**
         * Private Helper Functions for populateJournalTable()
         */

        /**
         * Build a map of active filters from filter row inputs
         * @param {NodeList} filterRowInputs - All filter input elements
         * @returns {Object} - Map of columnKey to filter value
         */
        function _buildJournalFiltersMap(filterRowInputs) {
            const activeFilters = {};
            filterRowInputs.forEach(input => {
                const thParent = input.closest('th');
                if (thParent) {
                    const columnKey = thParent.dataset.journalColumnKey;
                    const value = input.value.trim().toLowerCase();
                    if (value !== '') activeFilters[columnKey] = value;
                }
            });
            return activeFilters;
        }

        /**
         * Check if a session date matches a date filter with operators
         * @param {string} sessionDateString_YYYY_MM_DD - Session date in YYYY-MM-DD format
         * @param {string} filterValue - Filter value (may include operators like >=, <=, etc.)
         * @returns {boolean} - True if matches, false otherwise
         */
        function _matchesJournalDateFilter(sessionDateString_YYYY_MM_DD, filterValue) {
            if (!sessionDateString_YYYY_MM_DD) return false;

            let operator = null;
            let dateFilterStringUserInput = filterValue;

            // Parse operators
            if (dateFilterStringUserInput.startsWith(">=")) {
                operator = ">=";
                dateFilterStringUserInput = dateFilterStringUserInput.substring(2).trim();
            } else if (dateFilterStringUserInput.startsWith("<=")) {
                operator = "<=";
                dateFilterStringUserInput = dateFilterStringUserInput.substring(2).trim();
            } else if (dateFilterStringUserInput.startsWith(">")) {
                operator = ">";
                dateFilterStringUserInput = dateFilterStringUserInput.substring(1).trim();
            } else if (dateFilterStringUserInput.startsWith("<")) {
                operator = "<";
                dateFilterStringUserInput = dateFilterStringUserInput.substring(1).trim();
            }

            try {
                // Parse filter date (DD.MM.YYYY format)
                let filterDateObj = null;
                if (dateFilterStringUserInput.includes('.')) {
                    const parts = dateFilterStringUserInput.split('.');
                    if (parts.length === 3) {
                        filterDateObj = new Date(Date.UTC(parseInt(parts[2]), parseInt(parts[1]) - 1, parseInt(parts[0])));
                    }
                }

                // Parse session date
                const sessionParts = sessionDateString_YYYY_MM_DD.split('-');
                if (sessionParts.length !== 3) return false;
                const sessionDateObj = new Date(Date.UTC(parseInt(sessionParts[0]), parseInt(sessionParts[1]) - 1, parseInt(sessionParts[2])));

                // Apply operator comparisons
                if (operator && filterDateObj) {
                    if (operator === ">=" && sessionDateObj < filterDateObj) return false;
                    if (operator === "<=" && sessionDateObj > filterDateObj) return false;
                    if (operator === ">" && sessionDateObj <= filterDateObj) return false;
                    if (operator === "<" && sessionDateObj >= filterDateObj) return false;
                } else if (!operator) {
                    // String match
                    const formattedSessionDate = formatDateISOtoEuropean(sessionDateString_YYYY_MM_DD).toLowerCase();
                    if (!sessionDateString_YYYY_MM_DD.includes(dateFilterStringUserInput) && !formattedSessionDate.includes(dateFilterStringUserInput)) {
                        return false;
                    }
                }
            } catch (e) {
                return false;
            }

            return true;
        }

        /**
         * Check if a numeric cell value matches a numeric filter
         * @param {number} cellNumber - Numeric cell value
         * @param {string} filterValue - Filter value (may include operators)
         * @returns {boolean} - True if matches, false otherwise
         */
        function _matchesJournalNumericFilter(cellNumber, filterValue) {
            return matchesNumericFilter(cellNumber, filterValue);
        }

        /**
         * Apply all active filters to journal sessions
         * @param {Array} sessions - Array of journal sessions
         * @param {Object} activeFilters - Map of columnKey to filter value
         * @param {Array} numericFilterKeys - Array of keys that should be treated as numeric
         * @param {Function} getRigDisplayString - Helper to build rig display string
         * @returns {Array} - Filtered sessions
         */
        function _applyJournalFilters(sessions, activeFilters, numericFilterKeys, getRigDisplayString) {
            if (Object.keys(activeFilters).length === 0) return sessions;

            return sessions.filter(session => {
                for (const key in activeFilters) {
                    const filterValue = activeFilters[key];
                    const config = journalColumnConfig[key];
                    if (!config) continue;

                    // Special handling for Telescope Setup
                    if (key === 'telescope_setup_notes') {
                        const compositeString = getRigDisplayString(session).toLowerCase();
                        const rawNotes = String(session.telescope_setup_notes || '').toLowerCase();
                        if (!compositeString.includes(filterValue) && !rawNotes.includes(filterValue)) {
                            return false;
                        }
                        continue;
                    }

                    let rawSessionValueStr = String(session[config.dataKey] || '').toLowerCase();

                    // Date filter
                    if (key === 'date_utc') {
                        if (!_matchesJournalDateFilter(session[config.dataKey], filterValue)) {
                            return false;
                        }
                        continue;
                    }

                    // Numeric filter
                    if (numericFilterKeys.includes(key)) {
                        let sessionNumericValue = session[config.dataKey];
                        if (sessionNumericValue === null || sessionNumericValue === undefined) return false;
                        const cellNumber = parseFloat(sessionNumericValue);
                        if (isNaN(cellNumber)) {
                            if (!rawSessionValueStr.includes(filterValue)) return false;
                            continue;
                        }
                        if (!_matchesJournalNumericFilter(cellNumber, filterValue)) return false;
                    } else {
                        // Standard string check
                        if (filterValue.startsWith("!")) {
                            if (rawSessionValueStr.includes(filterValue.substring(1))) return false;
                        } else {
                            if (!rawSessionValueStr.includes(filterValue)) return false;
                        }
                    }
                }
                return true;
            });
        }

        /**
         * Sort journal sessions by the current sort configuration
         * @param {Array} sessions - Array of journal sessions
         * @param {Object} sortConfig - Column configuration for sorting
         * @param {Object} currentSort - Current sort state {columnKey, ascending}
         * @param {Array} numericFilterKeys - Array of keys that should be sorted numerically
         * @param {Function} getRigDisplayString - Helper to build rig display string
         * @returns {Array} - Sorted sessions
         */
        function _sortJournalSessions(sessions, sortConfig, currentSort, numericFilterKeys, getRigDisplayString) {
            if (!sortConfig) return sessions;

            return sessions.sort((a, b) => {
                let rawA = a[sortConfig.dataKey];
                let rawB = b[sortConfig.dataKey];

                // Special sort for setup: sort by the generated string
                if (sortConfig.dataKey === 'telescope_setup_notes') {
                    rawA = getRigDisplayString(a);
                    rawB = getRigDisplayString(b);
                }

                let valA_str = (rawA === null || rawA === undefined || rawA === '-') ? '' : String(rawA).toLowerCase();
                let valB_str = (rawB === null || rawB === undefined || rawB === '-') ? '' : String(rawB).toLowerCase();

                let comparison = 0;

                if (sortConfig.dataKey === 'date_utc') {
                    const dateA = new Date(a.date_utc || 0);
                    const dateB = new Date(b.date_utc || 0);
                    comparison = dateA - dateB;
                } else if (numericFilterKeys.includes(sortConfig.dataKey)) {
                    const numA = parseFloat(rawA) || 0;
                    const numB = parseFloat(rawB) || 0;
                    comparison = numA - numB;
                } else {
                    comparison = valA_str.localeCompare(valB_str);
                }

                if (comparison !== 0) {
                    return currentSort.ascending ? comparison : -comparison;
                }

                // Tie-breaker: sort by date descending
                const dateA_tie = new Date(a.date_utc || 0);
                const dateB_tie = new Date(b.date_utc || 0);
                return dateB_tie - dateA_tie;
            });
        }

        /**
         * Determine if target info should be shown for this row (grouping logic)
         * @param {string} currentTargetId - Current session's target ID
         * @param {string} previousTargetId - Previous session's target ID
         * @param {string} sortColumnKey - Current sort column key
         * @returns {boolean} - True if should show full target info
         */
        function _shouldShowGroupedTarget(currentTargetId, previousTargetId, sortColumnKey) {
            if (sortColumnKey !== 'object_name' && sortColumnKey !== 'target_common_name') {
                return true;
            }
            return currentTargetId !== previousTargetId;
        }

        /**
         * Create a single journal table row
         * @param {Object} session - Journal session object
         * @param {Object} config - Column configuration
         * @param {boolean} showFullTargetInfo - Whether to show target info
         * @param {Function} getRigDisplayString - Helper to build rig display string
         * @returns {HTMLTableRowElement} - Created row element
         */
        function _createJournalRow(session, config, showFullTargetInfo, getRigDisplayString) {
            const row = document.createElement('tr');
            row.classList.add('clickable-row');
            row.setAttribute('data-session-id', session.id);
            row.setAttribute('data-target-object-id', session.object_name);
            row.setAttribute('data-session-location', session.location_name || '');

            for (const key in journalColumnConfig) {
                const columnConfig = journalColumnConfig[key];
                const td = document.createElement('td');
                td.dataset.journalColumnKey = key;

                let rawValue = session[columnConfig.dataKey];
                let displayValue = "";

                // Custom check for Project Name
                if (key === 'project_name' && rawValue === '-') {
                    displayValue = "";
                }
                // Custom Display for Telescope Setup
                else if (key === 'telescope_setup_notes') {
                    const fullString = getRigDisplayString(session);
                    if (!fullString || fullString === "") {
                        displayValue = "N/A";
                    } else {
                        displayValue = fullString.length > 60 ? fullString.substring(0, 60) + "..." : fullString;
                        td.title = fullString;
                    }
                }
                // Standard N/A check
                else if (rawValue === null || rawValue === undefined || (typeof rawValue === 'string' && rawValue.trim() === "") || rawValue === 'N/A') {
                    displayValue = "N/A";
                }
                // Formatting
                else if (columnConfig.format) {
                    displayValue = columnConfig.format(rawValue);
                } else {
                    displayValue = String(rawValue);
                }

                // Apply grouping logic for target columns
                if ((columnConfig.dataKey === 'object_name' || columnConfig.dataKey === 'target_common_name') && !showFullTargetInfo) {
                    td.innerHTML = "";
                } else {
                    td.innerHTML = String(displayValue);
                }

                row.appendChild(td);
            }

            // Add click handler
            row.addEventListener('click', function() {
                const targetId = this.getAttribute('data-target-object-id');
                const sessionId = this.getAttribute('data-session-id');
                const sessionLoc = this.getAttribute('data-session-location');

                if (targetId && sessionId) {
                    let url = `/graph_dashboard/${encodeURIComponent(targetId)}?session_id=${encodeURIComponent(sessionId)}&tab=journal`;
                    if (sessionLoc) {
                        url += `&location=${encodeURIComponent(sessionLoc)}`;
                    }
                    window.location.href = url;
                }
            });

            return row;
        }

    function populateJournalTable() {
            const tableBody = document.getElementById('journal-data-body');
            if (!tableBody || !allJournalSessions) return;
    
            let sessionsToDisplay = [...allJournalSessions];
    
            // --- Helper to build the component string ---
            function getRigDisplayString(session) {
                const parts = [];
                if (session.telescope_name_snapshot) parts.push(session.telescope_name_snapshot);
                if (session.reducer_name_snapshot) parts.push(session.reducer_name_snapshot);
                if (session.camera_name_snapshot) parts.push(session.camera_name_snapshot);
    
                if (parts.length > 0) {
                    return parts.join(' + ');
                }
                // Fallback to old notes if no snapshot data exists
                return session.telescope_setup_notes || "";
            }
    
            // --- Filtering Logic ---
            const journalFilterInputs = document.querySelectorAll("#journal-filter-row input");
            const activeJournalFilters = _buildJournalFiltersMap(journalFilterInputs);
            const journalNumericFilterKeys = ['calculated_integration_time_minutes','guiding_rms_avg_arcsec','seeing_observed_fwhm','session_rating_subjective'];

            sessionsToDisplay = _applyJournalFilters(sessionsToDisplay, activeJournalFilters, journalNumericFilterKeys, getRigDisplayString);
    
            // --- Sorting Logic ---
            const sortConfig = journalColumnConfig[currentJournalSort.columnKey];
            sessionsToDisplay = _sortJournalSessions(sessionsToDisplay, sortConfig, currentJournalSort, journalNumericFilterKeys, getRigDisplayString);
    
            // --- Rendering Logic ---
            tableBody.innerHTML = '';
            let previousTargetIdForGrouping = null;

            sessionsToDisplay.forEach(session => {
                const showFullTargetInfoThisRow = _shouldShowGroupedTarget(session.object_name, previousTargetIdForGrouping, currentJournalSort.columnKey);
                const row = _createJournalRow(session, journalColumnConfig, showFullTargetInfoThisRow, getRigDisplayString);
                tableBody.appendChild(row);
                previousTargetIdForGrouping = session.object_name;
            });
            updateJournalSortIndicators();
        }
    
        function sortJournalTable(columnKey, toggle = true) {
            if (toggle) {
                if (currentJournalSort.columnKey === columnKey) { currentJournalSort.ascending = !currentJournalSort.ascending; }
                else { currentJournalSort.columnKey = columnKey; currentJournalSort.ascending = true; }
                localStorage.setItem("journal_sortOrder", currentJournalSort.ascending ? "asc" : "desc");
                localStorage.setItem("journal_sortColumnKey", columnKey);
            } else {
                const storedSortOrder = localStorage.getItem("journal_sortOrder");
                const storedSortColumnKey = localStorage.getItem("journal_sortColumnKey");
                if (storedSortColumnKey) {
                    currentJournalSort.columnKey = storedSortColumnKey;
                    currentJournalSort.ascending = (storedSortOrder === "asc");
                }
            }
            populateJournalTable();
        }
    
        function updateJournalSortIndicators() {
            document.querySelectorAll('#journal-data-table th .sort-indicator').forEach(span => span.innerHTML = '');
            const activeTh = document.querySelector(`#journal-data-table > thead > tr:not(.filter-row) > th[data-journal-column-key="${currentJournalSort.columnKey}"]`);
            if (activeTh) { const indicator = activeTh.querySelector('.sort-indicator'); if (indicator) { indicator.innerHTML = currentJournalSort.ascending ? '▲' : '▼'; } }
        }
    
        function filterJournalTable() { populateJournalTable(); }
        function saveJournalFilter(inputElement, columnKey) { localStorage.setItem("journal_filter_col_key_" + columnKey, inputElement.value); }
    
        // ========================================================================
        // window.onload Event Handler
        // ========================================================================
        window.onload = () => {
          // --- Nova Hook: Auto-load graph for Inspiration Modal (from any tab) ---
          // This wraps the original modal function to inject the graph logic globally
          const originalOpenInspirationModal = window.openInspirationModal;
          if (typeof originalOpenInspirationModal === 'function') {
              window.openInspirationModal = function(data, img, text, source, link) {
                  // 1. Open the modal as usual
                  originalOpenInspirationModal(data, img, text, source, link);
    
                  // 2. Trigger graph load automatically
                  setTimeout(() => {
                      // Handle data keys from both Table (.Object) and DB/Grid (.object_name)
                      const targetName = data.Object || data.object_name;
                      if (targetName && typeof loadInspirationGraph === 'function') {
                          loadInspirationGraph(targetName);
                      }
                  }, 100);
              };
          }
          // -----------------------------------------------------------------------
          // --- ADDED FOR SAVED VIEWS ---
          // Check if we are reloading to apply a view
          let viewToApplyOnLoad = localStorage.getItem('nova_last_applied_view');
          // --- END OF ADD ---
    
          // --- START: New Timer Logic ---
          let timeToNextUpdate = 60;
          const updateIntervalInSeconds = 60;
          const timerSpan = document.getElementById('next-update-timer');

          // Clear any existing intervals before creating new ones (prevents memory leaks on re-initialization)
          if (dataUpdateIntervalId) clearInterval(dataUpdateIntervalId);
          if (timerUpdateIntervalId) clearInterval(timerUpdateIntervalId);

          // This is the 60-second interval to fetch data
          dataUpdateIntervalId = setInterval(() => {
            if (document.getElementById('sim-mode-toggle')?.checked) return;
            fetchData(true); // <--- TRUE enables background mode (no progress bar)
            fetchSunEvents();
            timeToNextUpdate = updateIntervalInSeconds; // Reset the timer
            if (timerSpan) timerSpan.textContent = timeToNextUpdate + 's'; // Update text immediately
          }, updateIntervalInSeconds * 1000); // 60000ms

          // This is the new 1-second interval to update the countdown display
          timerUpdateIntervalId = setInterval(() => {
              if (document.getElementById('sim-mode-toggle')?.checked) return;
              timeToNextUpdate--; // Decrement the timer
              if (timeToNextUpdate < 0) {
                  timeToNextUpdate = 0; // Don't go below zero
              }
              if (timerSpan) {
                  timerSpan.textContent = timeToNextUpdate + 's';
              }
          }, 1000); // Run every 1 second

          // Clean up intervals when navigating away or reloading (prevents memory leaks)
          window.addEventListener('beforeunload', () => {
              if (dataUpdateIntervalId) clearInterval(dataUpdateIntervalId);
              if (timerUpdateIntervalId) clearInterval(timerUpdateIntervalId);
          });
          // --- END: New Timer Logic ---

          // activeTab = localStorage.getItem('activeTab') || 'position';  // Always use default (Dashboard tab)
          // const initialDsoSortColumnKey = localStorage.getItem("dso_sortColumnKey") || 'Altitude Current';
          // const initialDsoSortOrder = localStorage.getItem("dso_sortOrder") || 'desc';
          // currentSort.columnKey = initialDsoSortColumnKey;
          // currentSort.ascending = (initialDsoSortOrder === "asc");  // Always use default (Altitude descending)
          const initialJournalSortColumnKey = localStorage.getItem("journal_sortColumnKey") || 'session_date';
          const initialJournalSortOrder = localStorage.getItem("journal_sortOrder") || 'desc';
          currentJournalSort.columnKey = initialJournalSortColumnKey;
          currentJournalSort.ascending = (initialJournalSortOrder === "asc");
          const removeFiltersBtn = document.getElementById('remove-filters-btn');
          if (removeFiltersBtn) { removeFiltersBtn.addEventListener('click', clearAllFilters); }
    
          // --- ADDED FOR SAVED VIEWS ---
          savedViewsDropdown = document.getElementById('saved-views-dropdown');
          saveViewBtn = document.getElementById('save-view-btn');
          deleteViewBtn = document.getElementById('delete-view-btn');
          // --- END OF ADD ---
          if (IS_GUEST_USER) {
              if (savedViewsDropdown) savedViewsDropdown.disabled = true;
              if (saveViewBtn) saveViewBtn.style.display = 'none';
              if (deleteViewBtn) deleteViewBtn.style.display = 'none';
          }
    
          // --- FIX: Restore filters BEFORE fetching data ---
          // This ensures that when the data arrives, the filters are already in place
          for (const key in columnConfig) {
            if (columnConfig[key].filterable) {
                const val = localStorage.getItem("dso_filter_col_key_" + key);
                const inputEl = document.querySelector(`#data-table .filter-row th[data-column-key="${key}"] input`);
                if (val && inputEl) inputEl.value = val;
            }
          }
          for (const key in journalColumnConfig) {
            if (journalColumnConfig[key].filterable) {
                const val = localStorage.getItem("journal_filter_col_key_" + key);
                const inputEl = document.querySelector(`#journal-filter-row th[data-journal-column-key="${key}"] input`);
                if (val && inputEl) inputEl.value = val;
            }
          }
          // --- END FIX ---

          initializeSimulationMode();
          // Chain data fetches AFTER locations are loaded to ensure correct location value
          // This prevents using stale guest-mode location from sessionStorage
          fetchLocations().then(() => {
              fetchSunEvents();
              fetchData();
          });

          // --- ADD THIS LINE: Fetch saved views ---
          populateSavedViewsDropdown(viewToApplyOnLoad);
          // --- END OF ADD ---
    
          updateTabDisplay();

          document.querySelectorAll('.tab-button').forEach(button => {
            button.addEventListener('click', () => { activeTab = button.dataset.tab; updateTabDisplay(); });
          });
          document.querySelectorAll("#data-table > thead > tr:not(.filter-row) > th[data-column-key]").forEach(header => {
            const columnKey = header.dataset.columnKey;
            if (columnConfig[columnKey] && columnConfig[columnKey].sortable) { header.addEventListener("click", () => sortTable(columnKey, true)); }
          });
          document.querySelectorAll("#data-table .filter-row input").forEach(input => {
              const thParent = input.closest('th'); const columnKey = thParent ? thParent.dataset.columnKey : null;
              if (columnKey && columnConfig[columnKey] && columnConfig[columnKey].filterable) { input.addEventListener("keyup", () => { saveFilter(input, columnKey, 'dso'); filterTable(); }); }
          });
          document.querySelectorAll("#journal-data-table > thead > tr:not(.filter-row) > th[data-journal-column-key]").forEach(header => {
            const columnKey = header.dataset.journalColumnKey;
            if (journalColumnConfig[columnKey] && journalColumnConfig[columnKey].sortable) { header.addEventListener("click", () => sortJournalTable(columnKey, true)); }
          });
          document.querySelectorAll("#journal-filter-row input").forEach(input => {
              const thParent = input.closest('th'); const columnKey = thParent ? thParent.dataset.journalColumnKey : null;
              if (columnKey && journalColumnConfig[columnKey] && journalColumnConfig[columnKey].filterable) { input.addEventListener("keyup", () => { saveJournalFilter(input, columnKey); filterJournalTable(); }); }
          });
          document.querySelectorAll("#outlook-table th[data-outlook-column-key]").forEach(header => {
              const columnKey = header.dataset.outlookColumnKey;
              if (outlookColumnConfig[columnKey] && outlookColumnConfig[columnKey].sortable) {
                  header.addEventListener("click", (event) => {
                      if (event.target.tagName.toLowerCase() === 'input') { return; }
                      sortOutlookTable(columnKey, true);
                  });
              }
          });
          document.querySelectorAll("#outlook-filter-row input").forEach(input => {
              const thParent = input.closest('th');
              const columnKey = thParent ? thParent.dataset.outlookColumnKey : null;
              if (columnKey && outlookColumnConfig[columnKey] && outlookColumnConfig[columnKey].filterable) { input.addEventListener("keyup", filterOutlookTable); }
          });
    
          // --- ADDED FOR SAVED VIEWS ---
          if (saveViewBtn) saveViewBtn.addEventListener('click', handleSaveView);
          if (deleteViewBtn) deleteViewBtn.addEventListener('click', handleDeleteView);
          if (savedViewsDropdown) savedViewsDropdown.addEventListener('change', () => {
              const viewName = savedViewsDropdown.value;
    
              // Handle Delete Button Visibility
              if (!viewName) {
                  if (deleteViewBtn) deleteViewBtn.style.display = 'none';
              } else {
                  if (deleteViewBtn) deleteViewBtn.style.display = 'inline-block';
              }
    
              // --- FIX: Update "Remove Filter" button visibility immediately ---
              updateRemoveFiltersButtonVisibility();
    
              // --- LOGIC SPLIT ---
              // If on Heatmap, update in-place without reloading the page
              if (activeTab === 'heatmap') {
                  if (typeof updateHeatmapFilter === 'function') {
                      updateHeatmapFilter();
                  }
                  return;
              }
    
              // If on standard List tabs (Position/Properties/Journal), perform standard reload
              if (viewName) {
                loadView(viewName);
              } else {
                clearAllFilters();
              }
          });
    
          // --- NEW: Global Active Only Listener ---
          const globalActiveToggle = document.getElementById('global-active-toggle');
          if (globalActiveToggle) {
              globalActiveToggle.addEventListener('change', () => {
                  // 1. Update List Tables (Position / Properties)
                  filterTable();
    
                  // 2. Update Outlook Table
                  if (activeTab === 'outlook') renderOutlookTable();
    
                  // 3. Update Heatmap (if function exists)
                  if (typeof updateHeatmapFilter === 'function') {
                      updateHeatmapFilter();
                  }
    
                  // 4. Update Inspiration (re-renders based on filtered DSO list)
                  if (typeof renderInspirationGrid === 'function') {
                      renderInspirationGrid();
                  }
              });
          }
          // ----------------------------------------
    
          // --- END OF ADD ---
    
          // [Deleted the duplicate filter restoration block that was here]
    
           // --- (MOVED) Populate the dropdown. If viewToApplyOnLoad has a value, it will be selected. ---
           // This was moved to earlier in the function, right after fetchData()
    
           filterTable();
           filterJournalTable();
           updateRemoveFiltersButtonVisibility();
        };
    
        function renderOutlookTable() {
            const tableBody = document.getElementById('outlook-body');
            if (!tableBody) return;
    
            // --- NEW: Prepare Active Projects Set for fast lookup ---
            const activeOnlyToggle = document.getElementById('global-active-toggle');
            const isActiveOnly = activeOnlyToggle ? activeOnlyToggle.checked : false;
            const activeObjectsSet = new Set();
    
            if (isActiveOnly && window.latestDSOData) {
                window.latestDSOData.forEach(d => {
                    const ap = d.ActiveProject;
                    if (ap === true || ap === 1 || ap === '1' || ap === 'true') {
                        activeObjectsSet.add(d.Object);
                    }
                });
            }
            // --------------------------------------------------------
    
            const allFilters = {};
            document.querySelectorAll('#outlook-filter-row input').forEach(input => {
                const key = input.closest('th').dataset.outlookColumnKey;
                if (input.value.trim() !== '') allFilters[key] = input.value.trim().toLowerCase();
            });
            const dsoFilterKeys = ['Object', 'Common Name', 'Type', 'Constellation', 'Magnitude', 'Size', 'SB'];
            const dsoToOutlookKeyMap = { 'Object': 'object_name', 'Common Name': 'common_name', 'Type': 'type', 'Constellation': 'constellation', 'Magnitude': 'magnitude', 'Size': 'size', 'SB': 'sb' };
            dsoFilterKeys.forEach(dsoKey => {
                const input = document.querySelector(`#data-table .filter-row th[data-column-key="${dsoKey}"] input`);
                const outlookKey = dsoToOutlookKeyMap[dsoKey];
                if (input && input.value.trim() !== '' && outlookKey) { allFilters[outlookKey] = input.value.trim().toLowerCase(); }
            });
            let filteredData = allOutlookOpportunities.filter(opp => {
                // --- NEW: Active Only Check ---
                if (isActiveOnly && !activeObjectsSet.has(opp.object_name)) {
                    return false;
                }
                // ------------------------------
    
                return Object.keys(allFilters).every(key => {
                    const filterValue = allFilters[key]; const config = outlookColumnConfig[key];
                    if (key === 'rating') {
                        const numericRating = parseInt(opp.rating_num, 10);
                        if (isNaN(numericRating)) return false;
                        const match = filterValue.match(/([<>]=?)\s*(\d+)/);
                        if (match) { const op = match[1]; const num = parseInt(match[2], 10); if (op === ">") return numericRating > num; if (op === ">=") return numericRating >= num; if (op === "<") return numericRating < num; if (op === "<=") return numericRating <= num; }
                        else if (/^\d+$/.test(filterValue)) { return numericRating === parseInt(filterValue, 10); }
                        return String(opp.rating || '').toLowerCase().includes(filterValue);
                    }
    
                    // --- FIX: Strict filtering for Object Type ---
                    if (key === 'type') {
                        const cellText = String(opp[key] || '').toLowerCase();
                        // 1. Split the user's filter into distinct terms (e.g. "S", "G", "SNR")
                        const filterTypes = filterValue.split(/[\s,]+/).filter(t => t.length > 0);
                        if (filterTypes.length > 0) {
                            // 2. Split the object's type into distinct words (e.g. "SNR")
                            const cellTokens = cellText.split(/[\s,]+/).filter(t => t.length > 0);
                            // 3. Check if ANY filter term exists EXACTLY in the object's type words
                            // This prevents "S" from matching "SNR"
                            return filterTypes.some(term => cellTokens.includes(term));
                        }
                        return true;
                    }
                    // --- END FIX ---
    
                    const cellValue = String(opp[key] || '').toLowerCase();
                    if (config && config.numeric) {
                        const cellNumber = parseFloat(cellValue); if (isNaN(cellNumber)) return false;
                        const match = filterValue.match(/([<>]=?)\s*(\d+\.?\d*)/);
                        if (match) { const op = match[1]; const num = parseFloat(match[2]); if (op === ">") return cellNumber > num; if (op === ">=") return cellNumber >= num; if (op === "<") return cellNumber < num; if (op === "<=") return cellNumber <= num; }
                        return cellValue.includes(filterValue);
                    } else {
                        const filterTerms = filterValue.split(/[\s,]+/).filter(t => t.length > 0);
                        if (filterTerms.length === 0) return true;
                        return filterTerms.some(term => cellValue.includes(term));
                    }
                });
            });
            if (filteredData.length > 0) {
                let htmlRows = "";
                filteredData.forEach(target => {
                    htmlRows += `
                        <tr class="clickable-row" onclick="showGraph('${target.object_name}', '${target.date}', 'chart')">
                            <td data-outlook-column-key="object_name">${target.object_name}</td>
                            <td data-outlook-column-key="common_name">${target.common_name}</td>
                            <td data-outlook-column-key="date" style="text-align:center;">${formatDateISOtoEuropean(target.date)}</td>
                            <td data-outlook-column-key="max_alt" style="text-align:center;">${target.max_alt}°</td>
                            <td data-outlook-column-key="obs_dur" style="text-align:center;">${target.obs_dur} min</td>
                            <td data-outlook-column-key="rating" style="text-align:center;">${target.rating}</td>
                        </tr>
                    `;
                });
                tableBody.innerHTML = htmlRows;
            } else {
                tableBody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:20px; color:orange;">${window.t('no_targets_found')}</td></tr>`;
            }
        }
    
    
        function fetchOutlookData() {
            const tableBody = document.getElementById('outlook-body');
            const loadingDiv = document.getElementById("table-loading"); // Assuming you might have a general loading div
            if (!tableBody) return;
    
            // --- START FIX ---
            // Get the main DSO table body to check if a location update is already in progress
            const mainDsoTbody = document.getElementById("data-body");
            const isLocationUpdating = mainDsoTbody && mainDsoTbody.dataset.loading === 'true';
    
            // Show fetching message only if not already updating location UI
            if (!isLocationUpdating) {
            // --- END FIX ---
                tableBody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:20px; color:${getColor('--primary-dark', '#6795a4')};">${window.t('fetching')}</td></tr>`;
            }
    
            // --- START FIX ---
            // Get the currently selected location name from browser's session storage
            const currentSelectedLocation = sessionStorage.getItem('selectedLocation');
    
            // Check for Simulation Mode
            const simModeOn = document.getElementById('sim-mode-toggle')?.checked;
            const simDateVal = document.getElementById('sim-date-input')?.value;
    
            // Construct the URL with parameters
            let fetchUrl = `/get_outlook_data?location=${encodeURIComponent(currentSelectedLocation || '')}`;
            if (simModeOn && simDateVal) {
                fetchUrl += `&sim_date=${encodeURIComponent(simDateVal)}`;
            }
            // --- END FIX ---
    
            fetch(fetchUrl) // <-- Use the modified URL
                .then(response => response.json())
                .then(data => {
                    // --- FIX: Only set outlookDataLoaded to true when data is actually complete ---
                    // if (!outlookDataLoaded) { outlookDataLoaded = true; } // REMOVE THIS LINE (it was a bug)
    
                    if (data.status === 'complete') {
                        // --- FIX: Set loaded flag ONLY on complete ---
                        if (!outlookDataLoaded) { outlookDataLoaded = true; } // Correct placement
    
                        allOutlookOpportunities = data.results || []; // Store data globally
                        if (allOutlookOpportunities.length > 0) {
                            // Sort and render the table (assuming sortOutlookTable also calls renderOutlookTable)
                            sortOutlookTable(currentOutlookSort.columnKey, false);
                        } else {
                            tableBody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:20px; color:grey;">${window.t('no_imaging_opportunities')}</td></tr>`;
                        }
                    } else if (data.status === 'running' || data.status === 'starting') {
                        // Background task is running, show waiting message
                        if ((data.results || []).length > 0) {
                             // Optionally display stale data while waiting
                            allOutlookOpportunities = data.results || [];
                            sortOutlookTable(currentOutlookSort.columnKey, false);
                        } else {
                            tableBody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:20px; color:${getColor('--primary-dark', '#6795a4')};">${window.t('waiting_background_task')}</td></tr>`;
                        }
                        // Poll again after a delay
                        setTimeout(fetchOutlookData, 10000);
                    } else { // Handle 'idle' (should become 'starting' now) or 'error'
                        tableBody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:20px; color:orange;">${window.t('no_data_available')} ${data.message || ''}</td></tr>`;
                        // Optionally set outlookDataLoaded = true here too, to stop retrying on error
                         if (!outlookDataLoaded) { outlookDataLoaded = true; }
                    }
                    // Hide general loading indicator if used
                    if (loadingDiv) loadingDiv.style.display = 'none';
                })
                .catch(error => {
                    // outlookDataLoaded = true; // Set flag even on error to stop retries
                    console.error("Error fetching outlook data:", error);
                    tableBody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:20px; color:red;">${window.t('error_fetching_data')}</td></tr>`;
                    if (loadingDiv) loadingDiv.style.display = 'none';
                });
        }
    
        function degreesToCompass(deg) {
            const val = Math.floor((deg / 22.5) + 0.5);
            const arr = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
            return arr[val % 16];
        }
    
        async function loadInspirationGraph(objectName) {
            // 1. Locate Insertion Point (Heuristic based on "Constellation" label)
            // We look for elements containing the text "Constellation" to find the stats grid
            // Fix: Scope to modal if found, or strictly exclude main tables to prevent background injection
            const modal = document.getElementById('inspiration-modal');
            const allElements = (modal) ? modal.querySelectorAll('*') : document.querySelectorAll('*');
            let targetContainer = null;
    
            for (const el of allElements) {
                // Safety check: If we are searching the whole document, ignore the main table headers
                if (!modal && (el.closest('#data-table') || el.closest('#outlook-table'))) continue;
    
                if (el.textContent.trim() === 'Constellation' && el.offsetParent !== null) {
                    // Found the label. Traverse up to find the main stats grid container.
                    // Assuming structure: Grid -> Stat Box -> Label
                    // We want to append AFTER the Grid.
                    const statBox = el.parentElement;
                    if(statBox) {
                        const grid = statBox.parentElement;
                        if(grid) targetContainer = grid;
                    }
                    break;
                }
            }
    
            if (!targetContainer) return;
    
            // 2. Prepare Container
            let graphDiv = document.getElementById('inspiration-mini-graph');
            if (graphDiv) graphDiv.remove(); // Clean up previous if exists
    
            graphDiv = document.createElement('div');
            graphDiv.id = 'inspiration-mini-graph';
            graphDiv.style.width = '100%';
            // Strict height enforcement to prevent flex stretching
            graphDiv.style.height = '160px';
            graphDiv.style.minHeight = '160px';
            graphDiv.style.maxHeight = '160px';
            graphDiv.style.flex = '0 0 160px';
    
            graphDiv.style.marginTop = '2px';
            graphDiv.style.backgroundColor = getBgWhiteColor();
            graphDiv.style.marginBottom = '15px';
            // Removed position:relative and zIndex to prevent creating a stacking context that covers the footer
            targetContainer.parentNode.insertBefore(graphDiv, targetContainer.nextSibling);
    
            // Force a physical spacer at the very bottom of the container
            // This ensures space AFTER the source text, which padding on the parent sometimes fails to do in specific flex contexts
            let spacer = document.getElementById('inspiration-bottom-spacer');
            if (!spacer) {
                spacer = document.createElement('div');
                spacer.id = 'inspiration-bottom-spacer';
                spacer.style.height = '80px'; // Generous space to clear the footer
                spacer.style.width = '100%';
                spacer.style.flexShrink = '0'; // Prevent it from collapsing
                targetContainer.parentNode.appendChild(spacer);
            } else {
                // Ensure it's always the last element if it already existed (moves it to the bottom)
                targetContainer.parentNode.appendChild(spacer);
            }
    
            // 3. Fetch Data
            graphDiv.innerHTML = `<p style="text-align:center; color:${getColor('--text-muted', '#888')}; font-size:12px;">${window.t('loading_altitude_data')}</p>`;
    
            try {
                // FIX: Pass the current session location name. The backend will look up the fresh coordinates.
                const currentLoc = sessionStorage.getItem('selectedLocation') || '';
    
                // --- SIMULATION MODE SUPPORT ---
                const simModeOn = document.getElementById('sim-mode-toggle')?.checked;
                const simDateVal = document.getElementById('sim-date-input')?.value;
                let fetchUrl = `/api/get_plot_data/${encodeURIComponent(objectName)}?plot_loc_name=${encodeURIComponent(currentLoc)}`;
    
                if (simModeOn && simDateVal) {
                    fetchUrl += `&sim_date=${encodeURIComponent(simDateVal)}`;
                }
    
                const resp = await fetch(fetchUrl);
    
                if (!resp.ok) throw new Error("Data fetch failed");
                const data = await resp.json();
    
                // 4. Process Data
                // We only want the upcoming night (dusk to dawn) or a logical 24h window
                // The API returns ~24h from noon local.
    
                const times = [];
                const alts = [];
                const azs = []; // Store azimuths for annotation lookup
                const hoverTexts = [];
    
                data.times.forEach((t, i) => {
                    const alt = data.object_alt[i];
                    if (alt !== null) {
                        times.push(t);
                        alts.push(alt);
                        const az = data.object_az[i];
                        azs.push(az); // Capture azimuth
                        const dir = degreesToCompass(az);
                        const timeStr = new Date(t).toLocaleTimeString([], {hour: '2-digit', minute:'2-digit'});
                        hoverTexts.push(`Time: ${timeStr}<br>Alt: ${alt.toFixed(1)}°<br>Dir: ${dir} (${Math.round(az)}°)`);
                    }
                });
    
                graphDiv.innerHTML = ''; // Clear loading text
    
                // 5. Render Plotly
                const trace = {
                    x: times,
                    y: alts,
                    type: 'scatter',
                    mode: 'lines',
                    fill: 'tozeroy',
                    line: { color: getPrimaryColor(), width: 2 },
                    fillcolor: 'rgba(131, 180, 197, 0.2)',
                    text: hoverTexts,
                    hoverinfo: 'text'
                };
    
                // --- Calculate Day/Night Background Shapes ---
                const shapes = [];
    
                const sunEvents = (data.sun_events && data.sun_events.current) ? data.sun_events.current : {};
                const duskText = sunEvents.astronomical_dusk;
                const dawnText = sunEvents.astronomical_dawn;
                const graphDateStr = data.date; // Use the API date string to anchor calculations
    
                const dayShapeStyle = {
                    type: 'rect',
                    xref: 'x',
                    yref: 'paper',
                    y0: 0,
                    y1: 1,
                    fillcolor: getColor('--bg-light-gray-alt2', '#e0e0e0'),
                    layer: 'below',
                    line: { width: 0 }
                };
    
                // Helper to safely add 1 day to YYYY-MM-DD string
                const getNextDayIsoDate = (dateStr) => {
                    const d = new Date(dateStr);
                    d.setDate(d.getDate() + 1);
                    return d.toISOString().split('T')[0];
                };
    
                if (times.length > 0) {
                    const timeRegex = /^\d{1,2}:\d{2}$/;
                    const hasValidDusk = duskText && timeRegex.test(duskText);
                    const hasValidDawn = dawnText && timeRegex.test(dawnText);
    
                    if (hasValidDusk && hasValidDawn) {
                        // 1. Determine graph start/end dates based on the data array
                        // times[0] is roughly noon of graphDateStr
                        const startIso = times[0];
                        const endIso = times[times.length - 1];
    
                        // 2. Resolve Dusk ISO
                        // If Dusk is early morning (e.g. 01:00), it belongs to the NEXT day (Day+1) relative to graph start (Day Noon)
                        // If Dusk is afternoon/evening (e.g. 23:00), it belongs to CURRENT day (Day)
                        const [duskH] = duskText.split(':').map(Number);
                        let duskDateStr = graphDateStr;
                        if (duskH < 12) {
                            duskDateStr = getNextDayIsoDate(graphDateStr);
                        }
                        const duskIso = `${duskDateStr}T${duskText}:00`;
    
                        // 3. Resolve Dawn ISO
                        // Dawn is almost always the next day (Day+1), but we apply similar logic for safety or extreme latitudes
                        const [dawnH] = dawnText.split(':').map(Number);
                        let dawnDateStr = graphDateStr;
                        if (dawnH < 12) {
                            dawnDateStr = getNextDayIsoDate(graphDateStr);
                        }
                        const dawnIso = `${dawnDateStr}T${dawnText}:00`;
    
                        // Shape 1: Start -> Dusk
                        // Only draw if Dusk is actually after the graph start to prevent negative ranges expanding the axis
                        if (duskIso > startIso) {
                            shapes.push({ ...dayShapeStyle, x0: startIso, x1: duskIso });
                        }
    
                        // Shape 2: Dawn -> End
                        // Only draw if Dawn is actually before the graph end
                        if (dawnIso < endIso) {
                            shapes.push({ ...dayShapeStyle, x0: dawnIso, x1: endIso });
                        }
    
                    } else {
                        // Summer/Polar: No Astro Dark available
                        shapes.push({
                            ...dayShapeStyle,
                            x0: times[0],
                            x1: times[times.length - 1]
                        });
                    }
                }
    
                // Find peak altitude for annotation placement
                let maxIdx = 0;
                let maxVal = -1;
                for(let i=0; i<alts.length; i++) {
                    if(alts[i] > maxVal) { maxVal = alts[i]; maxIdx = i; }
                }
    
                const annotations = [];
                if(maxVal > 0 && azs[maxIdx] !== undefined) {
                    const compassDir = degreesToCompass(azs[maxIdx]);
                    annotations.push({
                        x: times[maxIdx],
                        y: maxVal,
                        xref: 'x',
                        yref: 'y',
                        text: compassDir, // e.g. "S"
                        showarrow: false,
                        font: { size: 11, color: getColor('--text-secondary', '#666') },
                        yshift: 8
                    });
                }
    
                const layout = {
                    height: 160,
                    margin: { t: 20, b: 30, l: 35, r: 10 }, // Increased top margin to fit the label
                    annotations: annotations,
                    shapes: shapes, // Add the calculated background shapes
                    xaxis: {
                        type: 'date',
                        tickformat: '%H:%M',
                        showgrid: false,
                        zeroline: false
                    },
                    yaxis: {
                        title: 'Alt (°)',
                        range: [0, 90],
                        autorange: false, // STRICTLY disable autorange to enforce 0-90
                        fixedrange: true,
                        showgrid: true,
                        gridcolor: getColor('--border-light', '#eee')
                    },
                    hovermode: 'closest',
                    showlegend: false,
                    paper_bgcolor: getBgWhiteColor(),
                    plot_bgcolor: getBgWhiteColor()
                };
    
                const config = { displayModeBar: false, responsive: true };
    
                Plotly.newPlot('inspiration-mini-graph', [trace], layout, config);
    
            } catch (e) {
                console.error(e);
                graphDiv.innerHTML = `<p style="text-align:center; color:${getColor('--text-muted', '#888')}; font-size:12px;">${window.t('altitude_data_unavailable')}</p>`;
            }
        }
    
        // --- REPLACED: New flash message script ---
        document.addEventListener("DOMContentLoaded", function () {
            const flashMessages = document.querySelectorAll(".flash-message");
            if (flashMessages.length > 0) {
                setTimeout(() => {
                    flashMessages.forEach(el => {
                        el.style.transition = "opacity 0.5s ease";
                        el.style.opacity = "0";
                        setTimeout(() => el.remove(), 500); // Remove from DOM after fade
                    });
                }, 4000); // Message disappears after 4 seconds
            }

            // --- Event listener for location selector ---
            const locationSelect = document.getElementById('location-select');
            if (locationSelect) {
                locationSelect.addEventListener('change', setLocation);
            }

            // --- Event listeners for "Ask Nova" feature ---
            const askNovaBtn = document.getElementById('ask-nova-btn');
            console.log('ask-nova-btn found:', askNovaBtn);

            if (askNovaBtn) {
                askNovaBtn.addEventListener('click', askNova);
            }

            // --- Event delegation for data-action attributes ---
            document.addEventListener('click', function(e) {
                const target = e.target.closest('[data-action]');
                if (!target) return;

                // Stop propagation if requested
                if (target.closest('[data-stop-propagation="true"]')) {
                    e.stopPropagation();
                    return;
                }

                const action = target.dataset.action;

                switch(action) {
                    case 'close-save-view-modal':
                        closeSaveViewModal();
                        e.preventDefault();
                        break;
                    case 'confirm-save-view':
                        confirmSaveView();
                        e.preventDefault();
                        break;
                    case 'close-banner':
                        target.parentElement.remove();
                        e.preventDefault();
                        break;
                }
            });
        });
        // --- END OF REPLACE ---

    // Expose functions needed by HTML inline event handlers and other scripts
    window.setLocation = setLocation;
    window.fetchLocations = fetchLocations;
    window.closeSaveViewModal = closeSaveViewModal;
    window.confirmSaveView = confirmSaveView;
    window.clearAllFilters = clearAllFilters;
    window.fetchData = fetchData;
    window.fetchSunEvents = fetchSunEvents;
    window.showGraph = showGraph;
    window.askNova = askNova;
    window.resetRanking = resetRanking;
})();
