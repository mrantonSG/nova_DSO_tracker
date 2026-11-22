// graph_view.js — Page logic for graph_view.html
// All Jinja2 data is read from window.NOVA_GRAPH_DATA and window.IS_GUEST_USER

(function() {
    "use strict";

    var DATA = window.NOVA_GRAPH_DATA || {};
    var objectName = DATA.objectName || '';

    // --- Tab requested by the URL ---
    // ?tab=framing&subtab=notes (dashboard Notes cell) means the Notes tab; a framing URL
    // (rig + ra/dec) or ?openframing=1 without a tab means the Framing tab.
    function tabFromUrl() {
        var params = new URLSearchParams(window.location.search);
        var tab = params.get('tab');
        if (tab === 'framing' && params.get('subtab') === 'notes') return 'notes';
        if (tab) return tab;
        if (params.has('rig') && (params.has('ra') || params.has('dec'))) return 'framing';
        if (params.get('openframing') === '1') return 'framing';
        return null;
    }

    // --- Patch: Update Separation on Date Change ---
    document.addEventListener("DOMContentLoaded", function() {
        var updateSep = async function() {
            var d = document.getElementById('day-select').value;
            var m = document.getElementById('month-select').value;
            var y = document.getElementById('year-select').value;
            try {
                var url = '/get_date_info/' + encodeURIComponent(objectName) + '?day=' + d + '&month=' + m + '&year=' + y + '&location=' + encodeURIComponent(DATA.plotLocName || '');
                var res = await fetch(url);
                var data = await res.json();
                if (data.separation && document.getElementById('separation-display')) {
                    document.getElementById('separation-display').textContent = data.separation + "\u00B0 " + window.t('sep');
                }
                if (data.phase !== undefined && document.getElementById('phase-display')) {
                    document.getElementById('phase-display').textContent = data.phase + "%";
                }
            } catch(e) { console.error("Sep update failed", e); }
        };

        ['day-select', 'month-select', 'year-select'].forEach(function(id) {
            var el = document.getElementById(id);
            if(el) el.addEventListener('change', updateSep);
        });
        document.querySelectorAll('.view-button').forEach(function(btn) {
            btn.addEventListener('click', function() { setTimeout(updateSep, 500); });
        });
    });

    // --- Sub-Tab Control Logic ---
    window.showProjectSubTab = function(tabName) {
        document.querySelectorAll('#notes-tab .detail-tab-content').forEach(function(tab) { tab.classList.remove('active'); });
        document.querySelectorAll('#notes-tab .detail-tab-button').forEach(function(button) { button.classList.remove('active'); });

        document.getElementById(tabName + '-sub-tab').classList.add('active');
        document.querySelector('#notes-tab .detail-tab-button[data-tab="' + tabName + '"]').classList.add('active');

        if (tabName === 'project-detail') {
            if (document.getElementById('project-view-mode')) {
                toggleProjectSubTabEdit(false, true);
            }
        }
    };

    // Trix helper for loading content into EDIT mode editors
    window.loadTrixContentEdit = function(editorId, htmlContent) {
        var editorElement = document.getElementById(editorId);
        if (editorElement && editorElement.editor) {
             editorElement.editor.loadHTML(htmlContent);
        } else if (editorElement) {
             var hiddenInput = document.getElementById(editorElement.getAttribute('input'));
             if (hiddenInput) {
                 hiddenInput.value = htmlContent;
             }
             editorElement.addEventListener('trix-initialize', function() {
                 editorElement.editor.loadHTML(hiddenInput.value);
             }, { once: true });
        }
    };

    // Toggle logic for the EDIT mode inside the Project Detail sub-tab
    window.toggleProjectSubTabEdit = function(enable, justSwitchedTab) {
        justSwitchedTab = justSwitchedTab || false;
        var viewMode = document.getElementById('project-view-mode');
        var editMode = document.getElementById('project-edit-mode');
        var editButton = document.getElementById('edit-button-project');

        if (!viewMode || !editMode) return;

        if (enable) {
            viewMode.style.display = 'none';
            editMode.style.display = 'block';
            if (editButton) editButton.style.display = 'none';

            loadTrixContentEdit('goals-editor-edit', document.getElementById('goals-hidden').value);
            loadTrixContentEdit('description-editor-edit', document.getElementById('description-hidden').value);
            loadTrixContentEdit('framing-editor-edit', document.getElementById('framing-hidden').value);
            loadTrixContentEdit('processing-editor-edit', document.getElementById('processing-hidden').value);

        } else {
            viewMode.style.display = 'block';
            editMode.style.display = 'none';
            if (editButton) editButton.style.display = 'inline-block';

            if (!justSwitchedTab) {
                window.location.href = window.location.href.split('?')[0] + '?tab=notes';
            }
        }
    };


    // --- Active Project Checkbox Handler ---
    (function(){
      var cb = document.getElementById('active-project-checkbox');
      if (!cb) return;
      cb.addEventListener('change', async function(e) {

        var isActive = e.target.checked;

        if (window.IS_GUEST_USER) {
            sessionStorage.setItem('nova_guest_active_' + objectName, isActive);
            console.log('Guest mode: Set ' + objectName + ' active status to ' + isActive + ' (visual/session only)');
            return;
        }

        try {
          var res = await fetch('/update_project_active', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ object: objectName, active: isActive })
          });

          var data = await res.json();
          if (!res.ok || data.status !== 'success') {
            await novaAlert(window.t('failed_update_active_project', { error: data.error || res.status }));
            e.target.checked = !e.target.checked;
          } else {
              console.log('Successfully updated Active Project status for ' + objectName + ' to ' + isActive);
          }
        } catch (err) {
          await novaAlert(window.t('failed_update_active_project', { error: err }));
          e.target.checked = !e.target.checked;
        }
      });
    })();


    // --- "New Target": the field is always visible; Create is enabled only once it
    // holds a non-blank name, and stays disabled while a create is running
    // (data-busy, set by createNewTarget). Enter in the field submits. ---
    (function () {
        const input = document.getElementById('new-target-name');
        const btn = document.getElementById('new-target-confirm');
        if (!input || !btn) return;
        const sync = function () {
            btn.disabled = btn.dataset.busy === '1' || input.value.trim() === '';
        };
        input.addEventListener('input', sync);
        input.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                if (!btn.disabled) btn.click();
            }
        });
        sync();
    })();

    // --- Event Delegation for data-action attributes (click events) ---
    document.addEventListener('click', function(e) {
        const actionBtn = e.target.closest('[data-action]');
        if (!actionBtn) return;

        var action = actionBtn.dataset.action;
        console.log('[GRAPH_VIEW] Click action triggered:', action, actionBtn);

        switch(action) {
            // Journal form actions - handled by inline script in _journal_section.html
            case 'save-draft':
            case 'save-close':
                // Let these events propagate to the inline script handlers
                return;
            case 'show-tab':
                console.log('[GRAPH_VIEW] show-tab:', actionBtn.dataset.tab);
                if (actionBtn.dataset.tab) {
                    showTab(actionBtn.dataset.tab);
                }
                break;
            case 'show-project-subtab':
                console.log('[GRAPH_VIEW] show-project-subtab:', actionBtn.dataset.tab);
                if (actionBtn.dataset.tab) {
                    showProjectSubTab(actionBtn.dataset.tab);
                }
                break;
            case 'toggle-project-edit':
                console.log('[GRAPH_VIEW] toggle-project-edit:', actionBtn.dataset.enable);
                var enable = actionBtn.dataset.enable === 'true';
                var justSwitched = actionBtn.dataset.justSwitched === 'true';
                toggleProjectSubTabEdit(enable, justSwitched);
                break;
            case 'navigate':
                console.log('[GRAPH_VIEW] navigate:', actionBtn.dataset.url);
                if (actionBtn.dataset.url) {
                    window.location.href = actionBtn.dataset.url;
                }
                break;
            case 'today': {
                console.log('[GRAPH_VIEW] today');
                var _now = new Date();
                var _dayEl = document.getElementById('day-select');
                var _monthEl = document.getElementById('month-select');
                var _yearEl = document.getElementById('year-select');
                if (_dayEl) _dayEl.value = String(_now.getDate()).padStart(2, '0');
                if (_monthEl) _monthEl.value = String(_now.getMonth() + 1);
                if (_yearEl) _yearEl.value = String(_now.getFullYear());
                if (typeof window.changeView === 'function') {
                    window.changeView('day');
                }
                if (window.NOVA_GRAPH_DATA) {
                    NOVA_GRAPH_DATA.selectedDay = parseInt(_dayEl.value, 10);
                    NOVA_GRAPH_DATA.selectedMonth = parseInt(_monthEl.value, 10);
                    NOVA_GRAPH_DATA.selectedYear = parseInt(_yearEl.value, 10);
                    NOVA_GRAPH_DATA.isSimMode = (
                        NOVA_GRAPH_DATA.selectedYear !== _now.getFullYear() ||
                        NOVA_GRAPH_DATA.selectedMonth !== (_now.getMonth() + 1) ||
                        NOVA_GRAPH_DATA.selectedDay !== _now.getDate()
                    );
                }
                if (typeof window._fetchCalibrationStar === 'function') {
                    window._fetchCalibrationStar();
                }
                break;
            }
            case 'change-view':
                console.log('[GRAPH_VIEW] change-view:', actionBtn.dataset.view, 'function exists:', typeof window.changeView);
                if (actionBtn.dataset.view && typeof window.changeView === 'function') {
                    window.changeView(actionBtn.dataset.view);
                } else {
                    console.error('[GRAPH_VIEW] changeView function not available!');
                }
                // Refresh dropdown when view changes (day/month/year view changes the date context)
                if (typeof fetchAndPopulateSecondarySelector === 'function') {
                    fetchAndPopulateSecondarySelector(NOVA_GRAPH_DATA.objectName);
                }
                // Sync date fields from inputs and re-fetch calibration star
                if (window.NOVA_GRAPH_DATA) {
                    var _dayEl = document.getElementById('day-select');
                    var _monthEl = document.getElementById('month-select');
                    var _yearEl = document.getElementById('year-select');
                    if (_dayEl) NOVA_GRAPH_DATA.selectedDay = parseInt(_dayEl.value, 10);
                    if (_monthEl) NOVA_GRAPH_DATA.selectedMonth = parseInt(_monthEl.value, 10);
                    if (_yearEl) NOVA_GRAPH_DATA.selectedYear = parseInt(_yearEl.value, 10);
                    var _now = new Date();
                    NOVA_GRAPH_DATA.isSimMode = (
                        NOVA_GRAPH_DATA.selectedYear !== _now.getFullYear() ||
                        NOVA_GRAPH_DATA.selectedMonth !== (_now.getMonth() + 1) ||
                        NOVA_GRAPH_DATA.selectedDay !== _now.getDate()
                    );
                }
                if (typeof window._fetchCalibrationStar === 'function') {
                    window._fetchCalibrationStar();
                }
                break;
            case 'save-project':
                console.log('[GRAPH_VIEW] save-project, function exists:', typeof window.saveProject);
                if (typeof window.saveProject === 'function') {
                    window.saveProject();
                } else {
                    console.error('[GRAPH_VIEW] saveProject function not available!');
                }
                break;
            case 'open-stellarium':
                console.log('[GRAPH_VIEW] open-stellarium');
                if (typeof window.openInStellarium === 'function') {
                    window.openInStellarium();
                }
                break;
            case 'flip-framing':
                console.log('[GRAPH_VIEW] flip-framing');
                if (typeof window.flipFraming90 === 'function') {
                    window.flipFraming90();
                }
                break;
            case 'copy-framing-url':
                console.log('[GRAPH_VIEW] copy-framing-url');
                if (typeof window.copyFramingUrl === 'function') {
                    window.copyFramingUrl();
                }
                break;
            case 'save-framing-db':
                console.log('[GRAPH_VIEW] save-framing-db');
                if (typeof window.saveFramingToDB === 'function') {
                    window.saveFramingToDB();
                }
                break;
            case 'scan-frame':
                window.scanFrameForDSOs();
                break;
            case 'new-target-confirm': {
                const input = document.getElementById('new-target-name');
                const btn = document.getElementById('new-target-confirm');
                if (btn && btn.disabled) break;
                if (typeof window.createNewTarget === 'function' && typeof window.frameCenter === 'function') {
                    // One snapshot, used for both the object's position and its framing.
                    const center = window.frameCenter();
                    window.createNewTarget(input ? input.value : '', center, center, undefined, btn);
                }
                break;
            }
            case 'close-scan-popup':
                const p = document.getElementById('scan-result-popup');
                if (p) p.hidden = true;
                break;
            case 'copy-mosaic-csv':
                console.log('[GRAPH_VIEW] copy-mosaic-csv');
                if (typeof window.copyAsiairMosaic === 'function') {
                    window.copyAsiairMosaic();
                }
                break;
            case 'copy-ra-dec':
                console.log('[GRAPH_VIEW] copy-ra-dec');
                if (typeof window.copyRaDec === 'function') {
                    window.copyRaDec();
                }
                break;
            case 'copy-result':
                if (typeof window.copyResult === 'function') {
                    window.copyResult(actionBtn.dataset.copy);
                }
                break;
            case 'recenter-fov':
                console.log('[GRAPH_VIEW] recenter-fov');
                if (typeof window.resetFovCenterToObject === 'function') {
                    window.resetFovCenterToObject();
                }
                break;
            case 'edit-project':
                console.log('[GRAPH_VIEW] edit-project');
                toggleProjectSubTabEdit(true);
                e.preventDefault();
                break;
            case 'cancel-project-edit':
                console.log('[GRAPH_VIEW] cancel-project-edit');
                toggleProjectSubTabEdit(false);
                e.preventDefault();
                break;
            default:
                console.warn('[GRAPH_VIEW] Unknown action:', action);
        }

        // Handle stop propagation AFTER processing data-action (if specified on element)
        if (actionBtn.dataset.stopPropagation === 'true') {
            e.stopPropagation();
        }
    });

    // --- Event Delegation for data-action attributes (change events) ---
    document.addEventListener('change', function(e) {
        const actionBtn = e.target.closest('[data-action]');
        if (!actionBtn) return;

        var action = actionBtn.dataset.action;
        console.log('[GRAPH_VIEW] Change action triggered:', action, actionBtn);

        switch(action) {
            case 'toggle-geo-belt':
                console.log('[GRAPH_VIEW] toggle-geo-belt:', actionBtn.checked);
                if (typeof window.toggleGeoBelt === 'function') {
                    window.toggleGeoBelt(actionBtn.checked);
                }
                break;
            case 'update-framing-rig':
                console.log('[GRAPH_VIEW] update-framing-rig');
                if (typeof window.applyRigChangeKeepingCentre === 'function') {
                    window.applyRigChangeKeepingCentre();
                }
                if (typeof window.updateFovVsObjectLabel === 'function') {
                    window.updateFovVsObjectLabel();
                }
                window.updateScanButtonState?.();
                break;
            case 'update-mosaic':
                console.log('[GRAPH_VIEW] update-mosaic');
                if (typeof window.updateFramingChart === 'function') {
                    window.updateFramingChart(false);
                }
                break;
            case 'change-survey':
                console.log('[GRAPH_VIEW] change-survey:', actionBtn.value);
                // setSurvey always builds the layer and never rejects, so there
                // is nothing to catch here.
                if (typeof window.setSurvey === 'function') {
                    window.setSurvey(actionBtn.value);
                }
                break;
            case 'set-location':
                console.log('[GRAPH_VIEW] set-location:', actionBtn.value);
                if (typeof window.setLocation === 'function') {
                    window.setLocation(actionBtn);
                }
                break;
        }
    });

    // --- Event Delegation for data-action attributes (input events - for range sliders) ---
    document.addEventListener('input', function(e) {
        const actionBtn = e.target.closest('[data-action]');
        if (!actionBtn) return;

        var action = actionBtn.dataset.action;
        console.log('[GRAPH_VIEW] Input action triggered:', action, actionBtn);

        switch(action) {
            case 'rotation-input':
                console.log('[GRAPH_VIEW] rotation-input:', actionBtn.value);
                if (typeof window.onRotationInput === 'function') {
                    window.onRotationInput(actionBtn.value);
                }
                break;
            case 'update-image-adjustments':
                console.log('[GRAPH_VIEW] update-image-adjustments');
                if (typeof window.updateImageAdjustments === 'function') {
                    window.updateImageAdjustments();
                }
                break;
        }
    });

    // --- Main Initialization Block (DOMContentLoaded) ---
    document.addEventListener('DOMContentLoaded', function(event) {
        // --- Guest/Trix Setup ---
        if (window.IS_GUEST_USER) {
            var trixEditor = document.getElementById('project-field-editor');
            var saveButton = document.querySelector('button[data-action="save-project"]');

            if (trixEditor) {
                trixEditor.addEventListener('trix-initialize', function() {
                    trixEditor.editor.element.setAttribute('disabled', 'true');
                });
            }
            if (saveButton) saveButton.style.display = 'none';

            var storageKey = 'nova_guest_active_' + objectName;
            var savedState = sessionStorage.getItem(storageKey);
            var checkbox = document.getElementById('active-project-checkbox');

            if (checkbox && savedState !== null) {
                checkbox.checked = (savedState === 'true');
            }
        }

        // --- Project Edit Form Submission Handler ---
        var editForm = document.getElementById('project-edit-form');
        if (editForm) {
            editForm.addEventListener('submit', function(e) {
                document.getElementById('goals-hidden').value = document.getElementById('goals-editor-edit').value;
                document.getElementById('description-hidden').value = document.getElementById('description-editor-edit').value;
                document.getElementById('framing-hidden').value = document.getElementById('framing-editor-edit').value;
                document.getElementById('processing-hidden').value = document.getElementById('processing-editor-edit').value;
            });
        }

        // --- Tab Initialization ---
        var params = new URLSearchParams(window.location.search);
        var periodFromUrl = params.get('period');

        var lastTab = null;
        try { lastTab = localStorage.getItem('lastActiveTab-' + JSON.stringify(objectName)); } catch (e) {}
        var tabToShow = tabFromUrl() || lastTab || 'chart';

        showTab(tabToShow);

        // --- Auto-Switch Chart View ---
        if (tabToShow === 'chart' && periodFromUrl === 'yearly') {
            setTimeout(function() {
                if (typeof changeView === 'function') {
                    console.log("Auto-switching to Year View based on URL parameter.");
                    changeView('year');
                }
            }, 300);
        }
    });


    // --- Original Helper Functions ---
    var opportunitiesLoaded = false;
    var simbadLoaded = false;

    var currentTab = null;

    window.showTab = function(tabName) {
        // Unknown tab (old link, stale localStorage): fall back to the chart
        if (!document.getElementById(tabName + '-tab') ||
            !document.querySelector('.tab-button[data-tab="' + tabName + '"]')) {
            console.warn('[GRAPH_VIEW] Unknown tab:', tabName);
            tabName = 'chart';
        }
        var previousTab = currentTab;
        currentTab = tabName;

        document.querySelectorAll('.tab-content').forEach(function(tab) { tab.classList.remove('active'); });
        document.querySelectorAll('.tab-button').forEach(function(button) { button.classList.remove('active'); });

        document.getElementById(tabName + '-tab').classList.add('active');
        document.querySelector('.tab-button[data-tab="' + tabName + '"]').classList.add('active');

        try { localStorage.setItem('lastActiveTab-' + JSON.stringify(objectName), tabName); } catch (e) {}

        // Framing tab lifecycle (graph_view_chart.js): Aladin, Lock overlay loop, keys
        var framingTab = window.novaFramingTab;
        if (framingTab) {
            if (previousTab === 'framing' && tabName !== 'framing') framingTab.hidden();
            if (tabName === 'framing' && previousTab !== 'framing') framingTab.shown();
        }

        if (tabName === 'opportunities' && !opportunitiesLoaded) {
            loadImagingOpportunities();
        }

        if (tabName === 'simbad' && !simbadLoaded) {
            loadSimbadInfo();
        }

        if (tabName === 'chart' && typeof window._fetchCalibrationStar === 'function') {
            window._fetchCalibrationStar();
        }
    };

    function loadSimbadInfo() {
        if (simbadLoaded) return;

        if (!navigator.onLine) {
            if (typeof displayOfflineMessage === 'function') {
                displayOfflineMessage('simbadContainer', window.t('simbad_requires_internet'));
            } else {
                document.getElementById('simbadContainer').innerHTML = '<div class="offline-message" style="min-height: 400px;">' + window.t('simbad_requires_internet_short') + '</div>';
            }
            simbadLoaded = true;
            return;
        }

        var iframe = document.getElementById('simbadIframe');
        if (!iframe) {
            // Fast path: the server already rendered the translated unavailable
            // message because the cache knows a mirror is current, and mirrors
            // refuse to be framed.
            simbadLoaded = true;
            return;
        }

        var container = document.getElementById('simbadContainer');
        var simbadHost = (window.NOVA_GRAPH_DATA && window.NOVA_GRAPH_DATA.simbadHost)
            || 'simbad.cds.unistra.fr';

        function showSimbadUnavailable() {
            container.innerHTML = '<div class="offline-message" style="min-height: 400px;">' + window.t('simbad_unavailable') + '</div>';
        }

        // The tab is opened lazily, so ask the server whether Strasbourg can be
        // embedded: it answers from its cache, or probes Strasbourg once.
        simbadLoaded = true;
        container.innerHTML = '<div class="offline-message" style="min-height: 400px;">' + window.t('loading') + '</div>';

        fetch('/api/simbad_status', { headers: { 'Accept': 'application/json' } })
            .then(function (response) {
                if (!response.ok) throw new Error('HTTP ' + response.status);
                return response.json();
            })
            .then(function (data) {
                if (!data || !data.embed) {
                    showSimbadUnavailable();
                    return;
                }
                container.innerHTML = '';
                container.appendChild(iframe);
                iframe.src = 'https://' + simbadHost + '/simbad/sim-basic?Ident=' + encodeURIComponent(objectName) + '&submit=SIMBAD+search';
            })
            .catch(showSimbadUnavailable);
    }

    /**
     * The /get_imaging_opportunities response built in the browser: NovaOpportunities
     * (static/js/nova_opportunities.js) on /api/desktop_inputs for the page's location,
     * through graph_view_chart.js's fetchPlotInputs() and its per-location cache. null
     * when the engine scripts or the location are missing, the inputs fail or are not
     * this location, or forObject() throws; the caller then asks the route.
     */
    async function localImagingOpportunities() {
        try {
            if (!(window.NovaAstro && window.NovaDashboardRows && window.NovaOutlook && window.NovaOpportunities
                    && typeof window._fetchPlotInputs === 'function')) {
                throw new Error('engine scripts not loaded');
            }
            var locName = DATA.plotLocName;
            if (!locName) throw new Error('no location');
            var inputs = await window._fetchPlotInputs(locName);
            if (!inputs.location || inputs.location.name !== locName) throw new Error('desktop_inputs: not this location');
            var results = window.NovaOpportunities.forObject({ inputs: inputs, objectName: objectName, nowMs: Date.now() });
            console.log('[opportunities] built in the browser: ' + results.length + ' nights');
            return { status: 'success', results: results };
        } catch (e) {
            console.warn('Client-side opportunities failed, using the server:', e);
            return null;
        }
    }

    async function loadImagingOpportunities() {
        if (opportunitiesLoaded) return;

        var tableBody = document.getElementById('opportunities-body');

        tableBody.innerHTML = '<tr class="loader-row"><td colspan="9">' + window.t('loading') + '</td></tr>';

        try {
            var plotLat = DATA.plotLat;
            var plotLon = DATA.plotLon;
            var plotTz = DATA.plotTz;

            var data = await localImagingOpportunities();
            if (!data) {
                var params = new URLSearchParams({
                    plot_lat: plotLat,
                    plot_lon: plotLon,
                    plot_tz: plotTz,
                    plot_loc_name: DATA.plotLocName || ''
                });

                var fetchUrl = '/get_imaging_opportunities/' + encodeURIComponent(objectName) + '?' + params.toString();

                var response = await fetch(fetchUrl);
                if (!response.ok) {
                    throw new Error('HTTP error! status: ' + response.status);
                }
                data = await response.json();
            }

            tableBody.innerHTML = '';

            if (data.status === 'success') {
                if (data.results && data.results.length > 0) {
                    data.results.forEach(function(opp) {
                        var row = document.createElement('tr');
                        var parts = opp.date.split('-');
                        var year = parts[0], month = parts[1], day = parts[2];
                        var url = '/graph_dashboard/' + encodeURIComponent(objectName) + '?year=' + parseInt(year) + '&month=' + parseInt(month) + '&day=' + parseInt(day) + '&tab=chart&location=' + encodeURIComponent(DATA.plotLocName || '');
                        row.style.cursor = 'pointer';
                        row.onclick = function() { window.location.href = url; };

                        var icsUrl = new URL('/generate_ics/' + encodeURIComponent(objectName), window.location.origin);
                        icsUrl.searchParams.append('date', opp.date);
                        icsUrl.searchParams.append('from_time', opp.from_time);
                        icsUrl.searchParams.append('to_time', opp.to_time);
                        icsUrl.searchParams.append('max_alt', opp.max_alt);
                        icsUrl.searchParams.append('moon_illum', opp.moon_illumination);
                        icsUrl.searchParams.append('obs_dur', opp.obs_minutes);
                        icsUrl.searchParams.append('lat', plotLat);
                        icsUrl.searchParams.append('lon', plotLon);
                        icsUrl.searchParams.append('tz', plotTz);

                        row.innerHTML =
                            '<td>' + formatDateISOtoEuropean(opp.date) + '</td>' +
                            '<td>' + opp.from_time + '</td>' +
                            '<td>' + opp.to_time + '</td>' +
                            '<td>' + opp.obs_minutes + '</td>' +
                            '<td>' + opp.max_alt + '</td>' +
                            '<td>' + opp.moon_illumination + '</td>' +
                            '<td>' + opp.moon_separation + '</td>' +
                            '<td>' + opp.rating + '</td>' +
                            '<td><a href="' + icsUrl.href + '" title="' + window.t('add_to_calendar') + '" style="font-size: 1.5em; text-decoration: none;" data-stop-propagation="true">\uD83D\uDCC5</a></td>';
                        tableBody.appendChild(row);
                    });
                } else {
                    tableBody.innerHTML = '<tr><td colspan="9">' + window.t('no_imaging_opportunities') + '</td></tr>';
                }
                opportunitiesLoaded = true;
            } else {
                tableBody.innerHTML = '<tr><td colspan="9">' + window.t('error_loading_opportunities', { error: window.escapeHtml(data.message || 'Unknown error') }) + '</td></tr>';
                opportunitiesLoaded = true;
            }
        } catch (error) {
            console.error('Error fetching imaging opportunities:', error);
            tableBody.innerHTML = '<tr><td colspan="9">' + window.t('failed_load_opportunities', { error: window.escapeHtml(error.message) }) + '</td></tr>';
            opportunitiesLoaded = true;
        }
    }

})();
