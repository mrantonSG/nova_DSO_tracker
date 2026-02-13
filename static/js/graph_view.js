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


    // --- Main Initialization Block (DOMContentLoaded) ---
    document.addEventListener('DOMContentLoaded', function(event) {
        // --- Guest/Trix Setup ---
        if (window.IS_GUEST_USER) {
            var trixEditor = document.getElementById('project-field-editor');
            var saveButton = document.querySelector('button[onclick="saveProject()"]');

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
                            '<td><a href="' + icsUrl.href + '" title="Add to calendar" style="font-size: 1.5em; text-decoration: none;" onclick="event.stopPropagation();">\uD83D\uDCC5</a></td>';
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
