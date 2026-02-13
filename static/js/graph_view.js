// graph_view.js — Page logic for graph_view.html
// All Jinja2 data is read from window.NOVA_GRAPH_DATA and window.IS_GUEST_USER

(function() {
    "use strict";

    var DATA = window.NOVA_GRAPH_DATA || {};
    var objectName = DATA.objectName || '';

    // --- Self-hiding flash messages ---
    document.addEventListener('DOMContentLoaded', function() {
        var flashMessages = document.querySelectorAll('.flash-message');
        if (flashMessages.length > 0) {
            setTimeout(function() {
                flashMessages.forEach(function(el) {
                    el.style.transition = 'opacity 0.5s ease';
                    el.style.opacity = '0';
                    setTimeout(function() { el.remove(); }, 500);
                });
            }, 4000);
        }
    });

    // --- Patch: Update Separation on Date Change ---
    document.addEventListener("DOMContentLoaded", function() {
        var updateSep = async function() {
            var d = document.getElementById('day-select').value;
            var m = document.getElementById('month-select').value;
            var y = document.getElementById('year-select').value;
            try {
                var url = '/get_date_info/' + encodeURIComponent(objectName) + '?day=' + d + '&month=' + m + '&year=' + y;
                var res = await fetch(url);
                var data = await res.json();
                if (data.separation && document.getElementById('separation-display')) {
                    document.getElementById('separation-display').textContent = data.separation + "\u00B0 sep";
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
        document.querySelectorAll('#framing-tab .detail-tab-content').forEach(function(tab) { tab.classList.remove('active'); });
        document.querySelectorAll('#framing-tab .detail-tab-button').forEach(function(button) { button.classList.remove('active'); });

        document.getElementById(tabName + '-sub-tab').classList.add('active');
        document.querySelector('#framing-tab .detail-tab-button[data-tab="' + tabName + '"]').classList.add('active');

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
                window.location.href = window.location.href.split('?')[0] + '?tab=framing';
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
            alert('Failed to update Active Project: ' + (data.error || res.status));
            e.target.checked = !e.target.checked;
          } else {
              console.log('Successfully updated Active Project status for ' + objectName + ' to ' + isActive);
          }
        } catch (err) {
          alert('Failed to update Active Project: ' + err);
          e.target.checked = !e.target.checked;
        }
      });
    })();


    // --- Event Delegation for data-action attributes (click events) ---
    document.addEventListener('click', function(e) {
        const actionBtn = e.target.closest('[data-action]');
        if (!actionBtn) return;

        var action = actionBtn.dataset.action;
        console.log('[GRAPH_VIEW] Click action triggered:', action, actionBtn);

        switch(action) {
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
            case 'change-view':
                console.log('[GRAPH_VIEW] change-view:', actionBtn.dataset.view, 'function exists:', typeof window.changeView);
                if (actionBtn.dataset.view && typeof window.changeView === 'function') {
                    window.changeView(actionBtn.dataset.view);
                } else {
                    console.error('[GRAPH_VIEW] changeView function not available!');
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
            case 'open-framing-assistant':
                console.log('[GRAPH_VIEW] open-framing-assistant, function exists:', typeof window.openFramingAssistant);
                if (typeof window.openFramingAssistant === 'function') {
                    // Check if there's saved framing data for this object
                    const objectName = window.NOVA_GRAPH_DATA?.objectName;
                    if (objectName) {
                        fetch(`/api/get_framing/${encodeURIComponent(objectName)}`)
                            .then(r => r.json())
                            .then(data => {
                                if (data.status === 'found') {
                                    // Reconstruct query string from DB data
                                    const params = new URLSearchParams();
                                    if (data.rig != null) params.set('rig', data.rig);
                                    if (data.ra != null) params.set('ra', data.ra);
                                    if (data.dec != null) params.set('dec', data.dec);
                                    if (data.rotation != null) params.set('rot', data.rotation);
                                    if (data.survey) params.set('survey', data.survey);
                                    if (data.blend) params.set('blend', data.blend);
                                    if (data.blend_op != null) params.set('blend_op', data.blend_op);

                                    // Restore Mosaic
                                    if (data.mosaic_cols != null) params.set('m_cols', data.mosaic_cols);
                                    if (data.mosaic_rows != null) params.set('m_rows', data.mosaic_rows);
                                    if (data.mosaic_overlap != null) params.set('m_ov', data.mosaic_overlap);

                                    // Open with saved framing
                                    window.openFramingAssistant(params.toString());
                                } else {
                                    // No saved framing, open with defaults
                                    window.openFramingAssistant();
                                }
                            })
                            .catch(err => {
                                console.error('[GRAPH_VIEW] Error fetching saved framing:', err);
                                // On error, open with defaults
                                window.openFramingAssistant();
                            });
                    } else {
                        window.openFramingAssistant();
                    }
                } else {
                    console.error('[GRAPH_VIEW] openFramingAssistant function not available!');
                }
                break;
            case 'open-stellarium':
                console.log('[GRAPH_VIEW] open-stellarium');
                if (typeof window.openInStellarium === 'function') {
                    window.openInStellarium();
                }
                break;
            case 'close-framing-assistant':
                console.log('[GRAPH_VIEW] close-framing-assistant');
                if (typeof window.closeFramingAssistant === 'function') {
                    window.closeFramingAssistant();
                } else {
                    console.error('[GRAPH_VIEW] closeFramingAssistant function not available!');
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
            case 'recenter-fov':
                console.log('[GRAPH_VIEW] recenter-fov');
                if (typeof window.resetFovCenterToObject === 'function') {
                    window.resetFovCenterToObject();
                }
                break;
            case 'nudge-fov':
                console.log('[GRAPH_VIEW] nudge-fov:', actionBtn.dataset.dx, actionBtn.dataset.dy);
                if (typeof window.nudgeFov === 'function') {
                    var dx = parseInt(actionBtn.dataset.dx) || 0;
                    var dy = parseInt(actionBtn.dataset.dy) || 0;
                    window.nudgeFov(dx, dy);
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
            case 'toggle-lock-fov':
                console.log('[GRAPH_VIEW] toggle-lock-fov:', actionBtn.checked);
                if (typeof window.applyLockToObject === 'function') {
                    window.applyLockToObject(actionBtn.checked);
                }
                break;
            case 'toggle-geo-belt':
                console.log('[GRAPH_VIEW] toggle-geo-belt:', actionBtn.checked);
                if (typeof window.toggleGeoBelt === 'function') {
                    window.toggleGeoBelt(actionBtn.checked);
                }
                break;
            case 'update-framing-rig':
                console.log('[GRAPH_VIEW] update-framing-rig');
                if (typeof window.updateFramingChart === 'function') {
                    window.updateFramingChart(true);
                }
                if (typeof window.updateFovVsObjectLabel === 'function') {
                    window.updateFovVsObjectLabel();
                }
                break;
            case 'update-mosaic':
                console.log('[GRAPH_VIEW] update-mosaic');
                if (typeof window.updateFramingChart === 'function') {
                    window.updateFramingChart(false);
                }
                break;
            case 'change-survey':
                console.log('[GRAPH_VIEW] change-survey:', actionBtn.value);
                if (typeof window.setSurvey === 'function') {
                    window.setSurvey(actionBtn.value);
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
        var tabFromUrl = params.get('tab');
        var periodFromUrl = params.get('period');

        var lastTab = localStorage.getItem('lastActiveTab-' + JSON.stringify(objectName));
        var tabToShow = tabFromUrl || lastTab || 'chart';

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

        // --- Initialize Project Sub-Tab ---
        if (tabToShow === 'framing') {
            showProjectSubTab('notes');
        }
    });


    // --- Original Helper Functions ---
    var opportunitiesLoaded = false;
    var simbadLoaded = false;

    window.showTab = function(tabName) {
        document.querySelectorAll('.tab-content').forEach(function(tab) { tab.classList.remove('active'); });
        document.querySelectorAll('.tab-button').forEach(function(button) { button.classList.remove('active'); });

        document.getElementById(tabName + '-tab').classList.add('active');
        document.querySelector('.tab-button[data-tab="' + tabName + '"]').classList.add('active');

        localStorage.setItem('lastActiveTab-' + JSON.stringify(objectName), tabName);

        if (tabName === 'opportunities' && !opportunitiesLoaded) {
            loadImagingOpportunities();
        }

        if (tabName === 'simbad' && !simbadLoaded) {
            loadSimbadInfo();
        }

        if (tabName === 'framing') {
            showProjectSubTab('notes');
        }
    };

    function loadSimbadInfo() {
        if (simbadLoaded) return;

        if (!navigator.onLine) {
            if (typeof displayOfflineMessage === 'function') {
                displayOfflineMessage('simbadContainer', 'SIMBAD requires an active internet connection to load data.');
            } else {
                document.getElementById('simbadContainer').innerHTML = '<div class="offline-message" style="min-height: 400px;">SIMBAD requires an active internet connection.</div>';
            }
            simbadLoaded = true;
            return;
        }

        var iframe = document.getElementById('simbadIframe');
        iframe.src = 'https://simbad.u-strasbg.fr/simbad/sim-basic?Ident=' + encodeURIComponent(objectName) + '&submit=SIMBAD+search';
        simbadLoaded = true;
    }

    async function loadImagingOpportunities() {
        if (opportunitiesLoaded) return;

        var tableBody = document.getElementById('opportunities-body');

        tableBody.innerHTML = '<tr class="loader-row"><td colspan="9">Loading...</td></tr>';

        try {
            var plotLat = DATA.plotLat;
            var plotLon = DATA.plotLon;
            var plotTz = DATA.plotTz;

            var params = new URLSearchParams({
                plot_lat: plotLat,
                plot_lon: plotLon,
                plot_tz: plotTz
            });

            var fetchUrl = '/get_imaging_opportunities/' + encodeURIComponent(objectName) + '?' + params.toString();

            var response = await fetch(fetchUrl);
            if (!response.ok) {
                throw new Error('HTTP error! status: ' + response.status);
            }
            var data = await response.json();

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
                            '<td>' + new Date(opp.date + 'T00:00:00Z').toLocaleDateString('en-GB', { day: '2-digit', month: '2-digit', year: 'numeric', timeZone: 'UTC' }) + '</td>' +
                            '<td>' + opp.from_time + '</td>' +
                            '<td>' + opp.to_time + '</td>' +
                            '<td>' + opp.obs_minutes + '</td>' +
                            '<td>' + opp.max_alt + '</td>' +
                            '<td>' + opp.moon_illumination + '</td>' +
                            '<td>' + opp.moon_separation + '</td>' +
                            '<td>' + opp.rating + '</td>' +
                            '<td><a href="' + icsUrl.href + '" title="Add to calendar" style="font-size: 1.5em; text-decoration: none;" data-stop-propagation="true">\uD83D\uDCC5</a></td>';
                        tableBody.appendChild(row);
                    });
                } else {
                    tableBody.innerHTML = '<tr><td colspan="9">No good imaging opportunities found within your search criteria.</td></tr>';
                }
                opportunitiesLoaded = true;
            } else {
                tableBody.innerHTML = '<tr><td colspan="9">Error loading opportunities: ' + (data.message || 'Unknown error') + '</td></tr>';
                opportunitiesLoaded = true;
            }
        } catch (error) {
            console.error('Error fetching imaging opportunities:', error);
            tableBody.innerHTML = '<tr><td colspan="9">Failed to load imaging opportunities. Check console for details. (' + error.message + ')</td></tr>';
            opportunitiesLoaded = true;
        }
    }

})();
