(function() {
    'use strict';

    // Guard against double initialization
    if (window.journalSectionInitialized) return;
    window.journalSectionInitialized = true;

    // --- Public API (Minimal Window Exposure) ---
    // ONLY expose functions called from Jinja-generated URLs
    window.loadSessionViaAjax = loadSessionViaAjax;

    // --- NATIVE PRINT: ROBUST & CLEAN PDF GENERATION ---
    async function downloadVisibleReport(defaultFilename, buttonElement, iframeId) {
        const iframe = document.getElementById(iframeId);
        if (!iframe || !iframe.contentWindow) {
             alert("Report frame not found.");
             return;
        }

        const buttonOriginalText = buttonElement.textContent;
        buttonElement.textContent = "Preparing Print View...";
        buttonElement.disabled = true;

        try {
            const doc = iframe.contentWindow.document;
            const contentDiv = doc.getElementById('report-content');
            const appendedContainer = doc.getElementById('appended-session-reports');

            // 1. FETCH & APPEND SESSIONS (If project report and not already loaded)
            const sessionRows = doc.querySelectorAll('.session-data-row');

            if (sessionRows.length > 0 && appendedContainer && appendedContainer.children.length === 0) {
                // Fetch Helper
                const fetchReport = async (url) => {
                    try { const response = await fetch(url); return await response.text(); }
                    catch (e) { console.error("Failed load: " + url); return null; }
                };

                for (let i = 0; i < sessionRows.length; i++) {
                    const reportUrl = sessionRows[i].dataset.reportUrl;
                    buttonElement.textContent = `Merging Session ${i+1}/${sessionRows.length}...`;
                    if (!reportUrl) continue;

                    const htmlContent = await fetchReport(reportUrl);
                    if (htmlContent) {
                        const parser = new DOMParser();
                        const sessionDoc = parser.parseFromString(htmlContent, 'text/html');
                        const sessionContent = sessionDoc.getElementById('report-content');

                        if (sessionContent) {
                            // A. Inject Session CSS (Only once) to fix column layouts
                            if (i === 0) {
                                sessionDoc.querySelectorAll('style').forEach(s => {
                                    const newStyle = doc.createElement('style');
                                    newStyle.textContent = s.textContent;
                                    doc.head.appendChild(newStyle);
                                });
                            }

                            // B. Create Wrapper with FORCED PAGE BREAK
                            const wrapper = doc.createElement('div');
                            wrapper.className = 'session-appendix-wrapper';
                            // CSS 'break-before: page' handles the magic automatically
                            wrapper.style.breakBefore = 'page';
                            wrapper.style.pageBreakBefore = 'always';
                            wrapper.style.marginTop = '0';
                            wrapper.style.marginBottom = '0';

                            // C. Add Clean Title - Nova Teal Brand Style
                            const title = doc.createElement('h3');
                            title.innerText = `APPENDIX: SESSION ${i+1}`;
                            title.style.color = (window.stylingUtils && window.stylingUtils.getPrimaryColor) ? window.stylingUtils.getPrimaryColor() : '#83b4c5';
                            title.style.fontSize = '11px';
                            title.style.textTransform = 'uppercase';
                            title.style.letterSpacing = '1px';
                            title.style.borderBottom = '0.5pt solid ' + ((window.stylingUtils && window.stylingUtils.getColor) ? window.stylingUtils.getColor('--border-light', '#eee') : '#eee');
                            title.style.paddingBottom = '5px';
                            title.style.marginBottom = '15px';
                            wrapper.appendChild(title);

                            // D. Append Content (Stripping Duplicate Headers)
                            Array.from(sessionContent.children).forEach(child => {
                                if (child.classList.contains('header-table') ||
                                    child.classList.contains('header-line') ||
                                    child.classList.contains('footer') ||
                                    child.classList.contains('target-info')) {
                                    return; // Skip duplicates
                                }
                                wrapper.appendChild(child.cloneNode(true));
                            });

                            appendedContainer.appendChild(wrapper);
                        }
                    }
                }
            }

            // 2. TRIGGER BROWSER PRINT
            buttonElement.textContent = "Check Popup...";
            setTimeout(() => {
                iframe.contentWindow.focus();
                iframe.contentWindow.print();
                buttonElement.textContent = buttonOriginalText;
                buttonElement.disabled = false;
            }, 500);

        } catch (err) {
            console.error(err);
            alert("Error preparing print view: " + err.message);
            buttonElement.textContent = buttonOriginalText;
            buttonElement.disabled = false;
        }
    }

    function showDetailTab(tabName) {
        const wrapper = document.getElementById('session-detail-wrapper');
        const isEditingOrAdding = wrapper.classList.contains('is-editing') || wrapper.classList.contains('is-adding');
        const modeSelector = isEditingOrAdding ? '.edit-mode-form' : '.view-mode';

        document.querySelectorAll(`${modeSelector} .detail-tab-content`).forEach(tab => tab.classList.remove('active'));
        document.querySelectorAll('.detail-tab-button').forEach(button => button.classList.remove('active'));

        const selectedTabContent = document.querySelector(`${modeSelector} #${tabName}-tab`);
        if (selectedTabContent) selectedTabContent.classList.add('active');

        const selectedTabButton = document.querySelector(`.detail-tab-button[data-tab="${tabName}"]`);
        if (selectedTabButton && !isEditingOrAdding) selectedTabButton.classList.add('active');

        // --- NEW: Lazy load the report preview if selecting the report tab ---
        if (tabName === 'report') {
            const iframe = document.getElementById('report-preview-frame');
            if (iframe && !iframe.src && iframe.dataset.src) {
                iframe.src = iframe.dataset.src; // Load the URL only when tab is clicked
            }
        }
    }

    function populateEditForm(data) {
        if (!data) return;
        const form = document.getElementById('journal-detail-form');

        // Fix: Explicitly set the location select value
        if (data.location_name) {
            form.elements['location_name'].value = data.location_name;
        }

        form.elements['seeing_observed_fwhm'].value = data.seeing_observed_fwhm || '';
        form.elements['sky_sqm_observed'].value = data.sky_sqm_observed || '';
        form.elements['moon_illumination_session'].value = data.moon_illumination_session || '';
        form.elements['moon_angular_separation_session'].value = data.moon_angular_separation_session || '';
        form.elements['rig_id_snapshot'].value = data.rig_id_snapshot || '';
        form.elements['telescope_setup_notes'].value = data.telescope_setup_notes || '';
        form.elements['guiding_rms_avg_arcsec'].value = data.guiding_rms_avg_arcsec || '';
        form.elements['exposure_time_per_sub_sec'].value = data.exposure_time_per_sub_sec || '';
        form.elements['number_of_subs_light'].value = data.number_of_subs_light || '';
        form.elements['filter_used_session'].value = data.filter_used_session || '';
        form.elements['gain_setting'].value = data.gain_setting || '';
        form.elements['offset_setting'].value = data.offset_setting || '';
        form.elements['session_rating_subjective'].value = data.session_rating_subjective || '';
        const journalEditor = document.getElementById('journal-notes-editor');
        if (journalEditor && journalEditor.editor) {
            journalEditor.editor.loadHTML(data.notes || '');
        } else {
            document.getElementById('journal-notes-hidden').value = data.notes || '';
        }
        form.elements['transparency_observed_scale'].value = data.transparency_observed_scale || '';
        form.elements['weather_notes'].value = data.weather_notes || '';
        form.elements['guiding_equipment'].value = data.guiding_equipment || '';
        form.elements['dither_details'].value = data.dither_details || '';
        form.elements['acquisition_software'].value = data.acquisition_software || '';
        form.elements['camera_temp_setpoint_c'].value = data.camera_temp_setpoint_c !== null ? data.camera_temp_setpoint_c : '';
        form.elements['camera_temp_actual_avg_c'].value = data.camera_temp_actual_avg_c !== null ? data.camera_temp_actual_avg_c : '';
        form.elements['binning_session'].value = data.binning_session || '';
        form.elements['darks_strategy'].value = data.darks_strategy || '';
        form.elements['flats_strategy'].value = data.flats_strategy || '';
        form.elements['bias_darkflats_strategy'].value = data.bias_darkflats_strategy || '';
        const monoFilters = ['L', 'R', 'G', 'B', 'Ha', 'OIII', 'SII'];
         monoFilters.forEach(filt => {
            form.elements[`filter_${filt}_subs`].value = data.hasOwnProperty(`filter_${filt}_subs`) && data[`filter_${filt}_subs`] !== null ? data[`filter_${filt}_subs`] : '';
            form.elements[`filter_${filt}_exposure_sec`].value = data.hasOwnProperty(`filter_${filt}_exposure_sec`) && data[`filter_${filt}_exposure_sec`] !== null ? data[`filter_${filt}_exposure_sec`] : '';
        });
        if (data.project_id) { form.elements['project_selection'].value = data.project_id; }
        else { form.elements['project_selection'].value = 'standalone'; }
        toggleNewProjectField();
    }

    function setupAddMode() {
        const wrapper = document.getElementById('session-detail-wrapper');
        const form = document.getElementById('journal-detail-form');
        const formDetailTitle = document.getElementById('form-detail-title');
        const submitButton = document.getElementById('form-submit-button');
        const cancelButton = document.getElementById('form-cancel-button');

        if (!wrapper || !form || !formDetailTitle || !submitButton || !cancelButton) return;

        // Hide ALL view modes (session and project)
        document.querySelectorAll('.view-mode, #journal-project-view-wrapper').forEach(el => el.style.display = 'none');

        // Hide Project Add Form if active
        const projAdd = document.getElementById('journal-project-add-mode');
        if(projAdd) projAdd.style.display = 'none';

        const initialDetailHeader = wrapper.querySelector('.detail-header:not(form .detail-header)');
        if(initialDetailHeader) initialDetailHeader.style.display = 'none';
        const placeholder = wrapper.querySelector('p.view-mode');
        if(placeholder) placeholder.style.display = 'none';

        form.style.display = 'block';
        wrapper.classList.remove('is-editing');
        wrapper.classList.add('is-adding');

        form.reset();
        const journalEditor = document.getElementById('journal-notes-editor');
        if (journalEditor && journalEditor.editor) journalEditor.editor.loadHTML('');

        // Note: These values will be populated by Jinja template context
        // They can't be dynamically set here without server-side context
        const formActionUrl = form.getAttribute('data-add-url');
        if (formActionUrl) form.action = formActionUrl;

        form.elements.session_id.value = '';

        const todayDate = form.getAttribute('data-today-date');
        if (todayDate) form.elements.session_date.value = todayDate;

        const defaultLocation = form.getAttribute('data-default-location');
        if (defaultLocation) form.elements.location_name.value = defaultLocation;

        form.elements['project_selection'].value = 'standalone';

        const deleteCheckbox = form.elements['delete_session_image']; if (deleteCheckbox) deleteCheckbox.checked = false;
        const fileInput = form.elements['session_image']; if(fileInput) fileInput.value = '';

        const titleText = form.getAttribute('data-add-title');
        if (titleText) formDetailTitle.textContent = titleText;
        else formDetailTitle.textContent = 'Add New Session';

        submitButton.textContent = 'Add Session';
        submitButton.style.backgroundColor = 'var(--success-color)';
        cancelButton.onclick = () => window.location.reload();

        updateMoonData();
        toggleNewProjectField();
    }

    function setupEditMode() {
        const wrapper = document.getElementById('session-detail-wrapper');
        const form = document.getElementById('journal-detail-form');
        const formDetailTitle = document.getElementById('form-detail-title');
        const submitButton = document.getElementById('form-submit-button');
        const cancelButton = document.getElementById('form-cancel-button');

        if (!wrapper || !form || !formDetailTitle || !submitButton || !cancelButton) return;

        document.querySelectorAll('.view-mode').forEach(el => el.style.display = 'none');
        const initialDetailHeader = wrapper.querySelector('.detail-header:not(form .detail-header)');
        if(initialDetailHeader) initialDetailHeader.style.display = 'none';

        form.style.display = 'block';
        wrapper.classList.add('is-editing');
        wrapper.classList.remove('is-adding');

        if (window.selectedSessionData) {
            formDetailTitle.textContent = 'Editing Session: ' + new Date(window.selectedSessionData.date_utc).toLocaleDateString('en-GB', { day: '2-digit', month: '2-digit', year: 'numeric' });
            form.action = `/journal/edit/${window.selectedSessionData.id}`;
            form.elements.session_id.value = window.selectedSessionData.id;
            form.elements.session_date.value = window.selectedSessionData.date_utc.split('T')[0];
            form.elements.location_name.value = window.selectedSessionData.location_name;
            populateEditForm(window.selectedSessionData);
            updateMoonData();
        }

        submitButton.textContent = 'Save Changes';
        submitButton.style.backgroundColor = 'var(--info-color-alt2)';
        cancelButton.onclick = cancelForm;
    }

    function cancelForm() {
        // Remove 'edit' parameter to ensure we return to View Mode (fixes loop after Duplicate)
        const url = new URL(window.location.href);
        url.searchParams.delete('edit');
        window.location.href = url.toString();
    }

    async function updateMoonData() {
        const dateInput = document.getElementById('session_date');
        const locationSelect = document.getElementById('location_name');
        if (!dateInput || !locationSelect) return;

        const date = dateInput.value;
        const selectedOption = locationSelect.options[locationSelect.selectedIndex];
        const illumInput = document.getElementById('moon_illumination_session');
        const sepInput = document.getElementById('moon_angular_separation_session');

        if (!date || !selectedOption || !selectedOption.dataset.lat) return;

        const lat = selectedOption.dataset.lat;
        const lon = selectedOption.dataset.lon;
        const tz = selectedOption.dataset.tz;

        // Get RA/DEC from data attributes on the form
        const form = document.getElementById('journal-detail-form');
        const ra = form ? form.getAttribute('data-object-ra') : '';
        const dec = form ? form.getAttribute('data-object-dec') : '';

        if (!ra || !dec) return;

        const apiUrl = `/api/get_moon_data?date=${date}&lat=${lat}&lon=${lon}&ra=${ra}&dec=${dec}&tz=${encodeURIComponent(tz)}`;
        try {
            const response = await fetch(apiUrl);
            if (!response.ok) throw new Error('Network response was not ok');
            const data = await response.json();

            if (data.status === 'success') {
                illumInput.value = data.moon_illumination !== null ? data.moon_illumination : '';
                sepInput.value = data.angular_separation !== null ? data.angular_separation : '';

                // Store duration globally for calculation
                window.currentObsDurationMinutes = data.observable_duration_min || 0;

                // Trigger recalculation for all existing inputs
                triggerAllMaxSubsCalculations();
            }
        } catch (error) {
            console.error('Failed to fetch moon data:', error);
        }
    }

    // Helper: Calculate total integration time currently scheduled by other fields
    function getUsedTimeSeconds(excludeKey) {
        let usedSeconds = 0;

        // Helper to add time if not the excluded key
        const addTime = (subsName, expName, currentKey) => {
            if (currentKey === excludeKey) return;
            const subs = parseFloat(document.querySelector(`input[name="${subsName}"]`)?.value) || 0;
            const exp = parseFloat(document.querySelector(`input[name="${expName}"]`)?.value) || 0;
            usedSeconds += (subs * exp);
        };

        // Check Main
        addTime('number_of_subs_light', 'exposure_time_per_sub_sec', 'main');

        // Check Mono Filters
        ['L', 'R', 'G', 'B', 'Ha', 'OIII', 'SII'].forEach(k => {
            addTime(`filter_${k}_subs`, `filter_${k}_exposure_sec`, k);
        });

        return usedSeconds;
    }

    // New Helper: Calculate Max Subs with Deduction and Efficiency
    function calculateMaxSubs(exposureSec, targetSpanId, filterKey) {
        const span = document.getElementById(targetSpanId);
        if (!span) return;

        const durationMin = window.currentObsDurationMinutes || 0;
        const exp = parseFloat(exposureSec);

        if (durationMin > 0 && exp > 0) {
            const totalAvailableSeconds = durationMin * 60;
            const usedSeconds = getUsedTimeSeconds(filterKey); // Get time used by *other* fields

            // Calculate remaining time for *this* field
            const remainingSeconds = Math.max(0, totalAvailableSeconds - usedSeconds);

            // Theoretical Max
            const maxSubs = Math.floor(remainingSeconds / exp);

            // Realistic Max (Efficiency factor ~85% for dither/meridian flip/readout)
            const efficiencyFactor = 0.85;
            const realSubs = Math.floor((remainingSeconds * efficiencyFactor) / exp);

            if (maxSubs > 0) {
                span.innerHTML = `(Max <span style="font-weight:600;">${maxSubs}</span> | Real <span style="font-weight:600;">${realSubs}</span>)`;
                span.title = `Based on ${durationMin}m total window minus other active filters. Real assumes ~15% overhead for meridian flip, dithering etc.`;
            } else {
                span.textContent = '(Time Limit Reached)';
                span.style.color = 'var(--danger-color)'; // Red warning
            }
        } else {
            span.textContent = '';
        }
    }

    function triggerAllMaxSubsCalculations() {
        // Main Exposure
        const mainExp = document.querySelector('input[name="exposure_time_per_sub_sec"]');
        if (mainExp) calculateMaxSubs(mainExp.value, 'max-subs-main', 'main');

        // Mono Filters
        const monoFilters = ['L', 'R', 'G', 'B', 'Ha', 'OIII', 'SII'];
        monoFilters.forEach(key => {
            const input = document.querySelector(`input[name="filter_${key}_exposure_sec"]`);
            if (input) calculateMaxSubs(input.value, `max-subs-${key}`, key);
        });
    }

    function toggleNewProjectField() {
        const projectSelect = document.getElementById('project_selection');
        const newProjectGroup = document.getElementById('new_project_name_group');
        if (projectSelect && newProjectGroup) {
            newProjectGroup.style.display = (projectSelect.value === 'new_project') ? 'block' : 'none';
        }
    }

    // Trix helper for loading content into editors (Used for Project Edit)
    function loadTrixContentJournal(editorId, htmlContent) {
        const editorElement = document.getElementById(editorId);
        if (editorElement && editorElement.editor) {
             editorElement.editor.loadHTML(htmlContent);
        } else if (editorElement) {
             const hiddenInput = document.getElementById(editorElement.getAttribute('input'));
             if (hiddenInput) {
                 hiddenInput.value = htmlContent;
             }
             editorElement.addEventListener('trix-initialize', function() {
                 editorElement.editor.loadHTML(hiddenInput.value);
             }, { once: true });
        }
    }

    function toggleJournalProjectEdit(enable) {
        const viewMode = document.getElementById('journal-project-view-mode');
        const editMode = document.getElementById('journal-project-edit-mode');

        // Select the button group in the main view header (Edit + Delete)
        const viewHeaderButtons = document.querySelector('#journal-project-view-wrapper .detail-header .action-button-group');

        if (!viewMode || !editMode) return;

        if (enable) {
            // Switch to Edit Mode
            viewMode.style.display = 'none';
            editMode.style.display = 'block';

            // Hide the Edit/Delete buttons in the main header
            if (viewHeaderButtons) viewHeaderButtons.style.display = 'none';

            // Ensure Trix editors are populated
            loadTrixContentJournal('goals-editor-journal', document.getElementById('goals-hidden-journal').value);
            loadTrixContentJournal('description-editor-journal', document.getElementById('description-hidden-journal').value);
            loadTrixContentJournal('framing-editor-journal', document.getElementById('framing-hidden-journal').value);
            loadTrixContentJournal('processing-editor-journal', document.getElementById('processing-hidden-journal').value);

        } else {
            // Switch to View Mode
            viewMode.style.display = 'flex';
            editMode.style.display = 'none';

            // Show the Edit/Delete buttons again
            if (viewHeaderButtons) viewHeaderButtons.style.display = 'flex';
        }
    }

    function setupAddProjectMode() {
        const wrapper = document.getElementById('session-detail-wrapper');
        const addProjDiv = document.getElementById('journal-project-add-mode');

        if (!wrapper || !addProjDiv) return;

        // 1. Hide View Elements
        document.querySelectorAll('.view-mode, #journal-project-view-wrapper').forEach(el => el.style.display = 'none');

        // 2. Hide Session Forms
        const sessForm = document.getElementById('journal-detail-form');
        if (sessForm) sessForm.style.display = 'none';

        // 3. Show Project Add Form
        addProjDiv.style.display = 'block';

        // 4. Hide headers
        const initialDetailHeader = wrapper.querySelector('.detail-header:not(form .detail-header)');
        if(initialDetailHeader) initialDetailHeader.style.display = 'none';

        // 5. Reset Form
        document.getElementById('journal-project-add-form').reset();
        const trix = document.getElementById('goals-editor-add');
        if(trix && trix.editor) trix.editor.loadHTML('');
    }

    // FIX: Auto-resize iframe to eliminate double scrollbars
    function resizeIframe(obj) {
        if (!obj || !obj.contentWindow) return;
        try {
            // Reset to allow shrinking if content changed, then match content height
            obj.style.height = (obj.contentWindow.document.body.scrollHeight + 50) + 'px';
        } catch(e) {
            // Fallback for safety
            console.warn("Iframe resize failed", e);
            obj.style.height = '1400px';
        }
    }

    // --- EXACT CHART GENERATOR (Uses real API & Chart.js) ---
    async function generateExactSessionChart(dateStr, locName) {
        // 1. Resolve Location Coordinates & Timezone
        // We scrape the dropdown to find the matching Lat/Lon/TZ for the session's location name
        let coords = { lat: window.NOVA_GRAPH_DATA.plotLat, lon: window.NOVA_GRAPH_DATA.plotLon, tz: window.NOVA_GRAPH_DATA.plotTz }; // Default
        const select = document.getElementById('location_name');
        if (select) {
            for (let opt of select.options) {
                if (opt.value === locName) {
                    coords = { lat: opt.dataset.lat, lon: opt.dataset.lon, tz: opt.dataset.tz };
                    break;
                }
            }
        }

        // 2. Prepare Date & API URL
        const dateObj = new Date(dateStr);
        const day = dateObj.getDate();
        const month = dateObj.getMonth() + 1;
        const year = dateObj.getFullYear();
        const objectName = window.NOVA_GRAPH_DATA.objectName;

        const apiUrl = `/api/get_plot_data/${encodeURIComponent(objectName)}?day=${day}&month=${month}&year=${year}&plot_lat=${coords.lat}&plot_lon=${coords.lon}&plot_tz=${encodeURIComponent(coords.tz)}`;

        try {
            // 3. Fetch Real Data
            const resp = await fetch(apiUrl);
            if (!resp.ok) return null;
            const data = await resp.json();

            // 4. Setup Canvas
            const canvas = document.createElement('canvas');
            canvas.width = 1000;
            canvas.height = 400; // 2.5:1 Aspect Ratio

            // 5. Replicate "Night Shade" Plugin (Gray background for day/twilight)
            const DateTime = luxon.DateTime;
            const nightShadePlugin = {
                id: 'nightShade',
                beforeDraw(chart) {
                    const {ctx, chartArea, scales} = chart;
                    if (!chartArea) return;

                    const x = scales.x;
                    const left = x.getPixelForValue(x.min);
                    const right = x.getPixelForValue(x.max);

                    // Parse times from API response
                    const baseDt = DateTime.fromISO(data.date, {zone: coords.tz});
                    const nextDt = baseDt.plus({days: 1});
                    const parseTime = (base, tStr) => {
                        if (!tStr || !tStr.includes(':')) return null;
                        const [h, m] = tStr.split(':').map(Number);
                        return base.set({hour: h, minute: m, second: 0}).toMillis();
                    };

                    // Logic: Draw gray box from Start->Dusk and Dawn->End to simulate "Night" window
                    let duskMs = parseTime(baseDt, data.sun_events.current.astronomical_dusk);
                    const sunsetMs = parseTime(baseDt, data.sun_events.current.sunset);
                    if (duskMs && sunsetMs && duskMs < sunsetMs) duskMs = parseTime(nextDt, data.sun_events.current.astronomical_dusk); // Rollover

                    const dawnMs = parseTime(nextDt, data.sun_events.next.astronomical_dawn);

                    const duskPx = duskMs ? x.getPixelForValue(duskMs) : right;
                    const dawnPx = dawnMs ? x.getPixelForValue(dawnMs) : left;

                    ctx.save();
                    ctx.fillStyle = (window.stylingUtils && window.stylingUtils.getCssVarAsRgba) ? window.stylingUtils.getCssVarAsRgba('--text-muted', '#888', 0.5) : 'rgba(211, 211, 211, 0.5)';

                    // Fill the "Daytime" areas
                    if (duskPx > left) ctx.fillRect(left, chartArea.top, duskPx - left, chartArea.height);
                    if (dawnPx < right) ctx.fillRect(dawnPx, chartArea.top, right - dawnPx, chartArea.height);
                    ctx.restore();
                }
            };

            // 6. Render Chart (Identical Config to Main View)
            const toMs = (val) => (typeof val === 'string') ? DateTime.fromISO(val, { zone: coords.tz }).toMillis() : val;

            new Chart(canvas, {
                type: 'line',
                data: {
                    labels: data.times.map(toMs),
                    datasets: [
                        {
                            label: `${objectName} Altitude`,
                            data: data.object_alt,
                            borderColor: (window.stylingUtils && window.stylingUtils.getChartLineColor) ? window.stylingUtils.getChartLineColor(0) : '#36A2EB',
                            borderWidth: 3,
                            pointRadius: 0,
                            tension: 0.1
                        },
                        {
                            label: 'Moon Altitude',
                            data: data.moon_alt,
                            borderColor: 'var(--warning-color)', // Yellow
                            borderWidth: 3,
                            pointRadius: 0,
                            tension: 0.1
                        },
                        {
                            label: 'Horizon',
                            data: Array(data.times.length).fill(0),
                            borderColor: (window.stylingUtils && window.stylingUtils.getColor) ? window.stylingUtils.getColor('--text-black', 'black') : 'black',
                            borderWidth: 2,
                            pointRadius: 0
                        }
                    ]
                },
                plugins: [nightShadePlugin],
                options: {
                    animation: false, // Instant render
                    responsive: false,
                    adapters: { date: { zone: coords.tz } },
                    scales: {
                        x: {
                            type: 'time',
                            time: { unit: 'hour', displayFormats: { hour: 'HH:mm' } },
                            grid: { color: 'rgba(128,128,128,0.5)', borderDash: [2, 2] },
                            title: { display: true, text: `Local Time (${coords.tz})` }
                        },
                        y: {
                            min: 0, max: 90,
                            title: { display: true, text: 'Altitude (°)' },
                            grid: { color: 'rgba(128,128,128,0.5)', borderDash: [2, 2] }
                        }
                    },
                    plugins: {
                        legend: { display: true, position: 'bottom' },
                        title: { display: true, text: `Visibility: ${dateStr}` }
                    }
                }
            });

            return canvas.toDataURL('image/png');

        } catch (e) {
            console.error("Report Chart Error:", e);
            return null;
        }
    }

    function showProjectTab(tabName) {
        // 1. Hide all content divs
        document.querySelectorAll('.project-tab-content').forEach(el => el.style.display = 'none');

        // 2. Remove active class from buttons
        document.querySelectorAll('.project-tab-btn').forEach(el => el.classList.remove('active'));

        // 3. Show selected content
        const content = document.getElementById(`project-${tabName}-tab`);
        if (content) content.style.display = 'block';

        // 4. Activate button
        const btn = document.querySelector(`.project-tab-btn[data-ptab="${tabName}"]`);
        if (btn) btn.classList.add('active');

        // 5. Lazy load report iframe if needed
        if (tabName === 'report') {
            const iframe = document.getElementById('project-report-frame');
            if (iframe && !iframe.src && iframe.dataset.src) {
                iframe.src = iframe.dataset.src;
            }
        }
    }

    async function loadSessionViaAjax(e, url, rowElement) {
        // Allow Ctrl/Cmd+Click to open in new tab
        if (e.ctrlKey || e.metaKey) return;

        e.preventDefault();
        document.body.style.cursor = 'wait';

        // UI Feedback: Reset all highlights first
        document.querySelectorAll('.clickable-session-row').forEach(r => r.classList.remove('current-session-item'));
        document.querySelectorAll('.project-header-clickable').forEach(r => r.classList.remove('current-project-item'));

        // Apply highlight based on the type of element clicked
        if (rowElement) {
            if (rowElement.classList.contains('project-header-clickable')) {
                rowElement.classList.add('current-project-item');
            } else {
                rowElement.classList.add('current-session-item');
            }
        }

        try {
            const response = await fetch(url);
            if (!response.ok) throw new Error("Network response was not ok");

            const text = await response.text();
            const parser = new DOMParser();
            const newDoc = parser.parseFromString(text, 'text/html');

            // 1. Swap the Detail Column Content
            const newContent = newDoc.querySelector('.session-detail-column');
            const currentContent = document.querySelector('.session-detail-column');

            if (newContent && currentContent) {
                currentContent.innerHTML = newContent.innerHTML;
            }

            // 2. Update Global State (Critical for Edit Mode to work after swap)
            const scripts = newDoc.querySelectorAll('script');
            for (let s of scripts) {
                // Extract the new session JSON data
                const match = s.textContent.match(/window\.selectedSessionData\s*=\s*(\{[\s\S]*?\});/);
                if (match && match[1]) {
                    try {
                        window.selectedSessionData = JSON.parse(match[1]);
                    } catch (err) { console.error("JSON Parse error", err); }
                    break;
                }
            }

            // 3. Update Browser URL without reloading
            window.history.pushState({}, '', url);

            // 4. Re-initialize Tabs if needed
            if (typeof showDetailTab === 'function') {
                showDetailTab('summary');
            }

        } catch (error) {
            console.warn("AJAX Load Failed, falling back to reload:", error);
            window.location.href = url;
        } finally {
            document.body.style.cursor = 'default';
        }
    }

    // --- Event Delegation ---
    function attachClickDelegation() {
        document.addEventListener('click', function(e) {
            const actionBtn = e.target.closest('[data-action]');
            if (!actionBtn) return;

            const action = actionBtn.dataset.action;
            console.log('[JOURNAL_SECTION] Click action triggered:', action, actionBtn);

            switch(action) {
                case 'add-session':
                    console.log('[JOURNAL_SECTION] add-session');
                    e.preventDefault();
                    setupAddMode();
                    break;
                case 'add-project':
                    console.log('[JOURNAL_SECTION] add-project');
                    e.preventDefault();
                    setupAddProjectMode();
                    break;
                case 'edit-session':
                    console.log('[JOURNAL_SECTION] edit-session');
                    e.preventDefault();
                    setupEditMode();
                    break;
                case 'cancel-form':
                    console.log('[JOURNAL_SECTION] cancel-form');
                    e.preventDefault();
                    cancelForm();
                    break;
                case 'show-detail-tab':
                    console.log('[JOURNAL_SECTION] show-detail-tab:', actionBtn.dataset.tab);
                    e.preventDefault();
                    showDetailTab(actionBtn.dataset.tab);
                    break;
                case 'show-project-tab':
                    console.log('[JOURNAL_SECTION] show-project-tab:', actionBtn.dataset.ptab);
                    e.preventDefault();
                    showProjectTab(actionBtn.dataset.ptab);
                    break;
                case 'download-report':
                    console.log('[JOURNAL_SECTION] download-report:', actionBtn.dataset.filename);
                    e.preventDefault();
                    downloadVisibleReport(
                        actionBtn.dataset.filename,
                        actionBtn,
                        actionBtn.dataset.iframeId
                    );
                    break;
                case 'toggle-project-edit':
                    console.log('[JOURNAL_SECTION] toggle-project-edit:', actionBtn.dataset.enable);
                    e.preventDefault();
                    toggleJournalProjectEdit(actionBtn.dataset.enable === 'true');
                    break;
                case 'load-session':
                    console.log('[JOURNAL_SECTION] load-session:', actionBtn.dataset.url);
                    loadSessionViaAjax(e, actionBtn.dataset.url, actionBtn);
                    break;
                case 'trigger-file-input':
                    console.log('[JOURNAL_SECTION] trigger-file-input:', actionBtn.dataset.targetId);
                    e.preventDefault();
                    const fileInput = document.getElementById(actionBtn.dataset.targetId);
                    if (fileInput) fileInput.click();
                    break;
            }
        });
    }

    function attachFormListeners() {
        // Confirmation dialogs for delete forms
        document.addEventListener('submit', function(e) {
            const confirmMsg = e.target.dataset.confirm;
            if (confirmMsg && !confirm(confirmMsg)) {
                e.preventDefault();
            }
        });

        // Form submission handler for the journal project edit form to update hidden inputs
        const projectEditForm = document.getElementById('journal-project-edit-form');
        if (projectEditForm) {
            projectEditForm.addEventListener('submit', function(e) {
                document.getElementById('goals-hidden-journal').value = document.getElementById('goals-editor-journal').value;
                document.getElementById('description-hidden-journal').value = document.getElementById('description-editor-journal').value;
                document.getElementById('framing-hidden-journal').value = document.getElementById('framing-editor-journal').value;
                document.getElementById('processing-hidden-journal').value = document.getElementById('processing-editor-journal').value;
            });
        }

        // New Project Form Listener
        const projectAddForm = document.getElementById('journal-project-add-form');
        if (projectAddForm) {
            projectAddForm.addEventListener('submit', function(e) {
                document.getElementById('goals-hidden-add').value = document.getElementById('goals-editor-add').value;
            });
        }

        // --- ASIAIR LOG IMPORT HANDLER ---
        const logInput = document.getElementById('asiair_log_input');
        if (logInput) {
            logInput.addEventListener('change', async function() {
                if (this.files.length === 0) return;

                const formData = new FormData();
                formData.append('file', this.files[0]);

                // Show loading state on button
                const btn = this.nextElementSibling;
                const originalText = btn.textContent;
                btn.textContent = "Parsing...";
                btn.disabled = true;

                try {
                    const response = await fetch('/api/parse_asiair_log', {
                        method: 'POST',
                        body: formData
                    });

                    if (!response.ok) throw new Error("Parse failed");

                    const data = await response.json();

                    if (data.status === 'success') {
                        const editor = document.getElementById('journal-notes-editor');
                        if (editor && editor.editor) {
                            // Insert content at the beginning
                            editor.editor.setSelectedRange([0, 0]);
                            editor.editor.insertHTML(data.html);
                            // Add a break after
                            editor.editor.insertLineBreak();
                        }
                    } else {
                        alert("Error parsing log: " + data.message);
                    }
                } catch (e) {
                    console.error(e);
                    alert("Failed to upload/parse log file.");
                } finally {
                    btn.textContent = originalText;
                    btn.disabled = false;
                    this.value = ''; // Reset input
                }
            });
        }
    }

    function attachInputListeners() {
        // Direct listeners for specific form fields
        const sessionDate = document.getElementById('session_date');
        const locationName = document.getElementById('location_name');
        const projectSelection = document.getElementById('project_selection');

        if (sessionDate) sessionDate.addEventListener('change', updateMoonData);
        if (locationName) locationName.addEventListener('change', updateMoonData);
        if (projectSelection) projectSelection.addEventListener('change', toggleNewProjectField);

        // Class-based delegation for calculation triggers
        document.addEventListener('input', function(e) {
            if (e.target.classList.contains('calc-trigger')) {
                console.log('[JOURNAL_SECTION] Input calc-trigger:', e.target.name || e.target.id);
                triggerAllMaxSubsCalculations();
            }
        });
    }

    function initializeFormState() {
        const wrapper = document.getElementById('session-detail-wrapper');
        const form = document.getElementById('journal-detail-form');

        // Initial visibility & population logic
        const startInEditMode = wrapper && wrapper.classList.contains('is-editing');

        if (startInEditMode) {
            // If we loaded in edit mode (e.g. duplicate), ensure form is visible and populated
            if (form) form.style.display = 'block';

            if (window.selectedSessionData) {
                const formDetailTitle = document.getElementById('form-detail-title');
                if (formDetailTitle) {
                    formDetailTitle.textContent = 'Editing Session: ' + new Date(window.selectedSessionData.date_utc).toLocaleDateString('en-GB', { day: '2-digit', month: '2-digit', year: 'numeric' });
                }
                if (form) {
                    form.action = `/journal/edit/${window.selectedSessionData.id}`;
                    form.elements.session_id.value = window.selectedSessionData.id;
                    form.elements.session_date.value = window.selectedSessionData.date_utc.split('T')[0];
                    if (form.elements.location_name && window.selectedSessionData.location_name) {
                        form.elements.location_name.value = window.selectedSessionData.location_name;
                    }
                }
                populateEditForm(window.selectedSessionData);
            }
        } else {
            // Standard view mode: hide the form initially
            if (form) form.style.display = 'none';
        }

         if (wrapper && (wrapper.classList.contains('is-adding') || wrapper.classList.contains('is-editing'))) {
            updateMoonData();
        }

        if(wrapper && wrapper.querySelector('.view-mode .detail-header')){
            showDetailTab('summary');
        }
    }

    // --- Initialization ---
    document.addEventListener('DOMContentLoaded', function() {
        attachClickDelegation();
        attachFormListeners();
        attachInputListeners();
        initializeFormState();
    });

    // ============================================
    // THEME INTEGRATION
    // ============================================

    /**
     * Update journal chart with theme-aware colors
     * Updates grid colors, axis colors, and re-renders chart
     */
    function updateJournalChartForTheme() {
        if (!window.journalChart) return;

        const isDark = window.stylingUtils && window.stylingUtils.isDarkTheme
            ? window.stylingUtils.isDarkTheme()
            : false;

        // Theme-aware colors
        const gridColor = isDark ? 'rgba(150, 150, 150, 0.3)' : 'rgba(128, 128, 128, 0.5)';
        const textColor = isDark ? '#e0e0e0' : '#333';
        const tickColor = isDark ? '#b0b0b0' : '#666';

        // Update chart options with new colors
        if (window.journalChart.options.scales) {
            window.journalChart.options.scales.x.grid.color = gridColor;
            window.journalChart.options.scales.y.grid.color = gridColor;

            // Update tick colors
            if (window.journalChart.options.scales.x.ticks) {
                window.journalChart.options.scales.x.ticks.color = tickColor;
            }
            if (window.journalChart.options.scales.y.ticks) {
                window.journalChart.options.scales.y.ticks.color = tickColor;
            }

            // Update title colors
            if (window.journalChart.options.scales.x.title) {
                window.journalChart.options.scales.x.title.color = textColor;
            }
            if (window.journalChart.options.scales.y.title) {
                window.journalChart.options.scales.y.title.color = textColor;
            }
        }

        // Update dataset colors
        if (window.journalChart.data.datasets) {
            // Object altitude
            window.journalChart.data.datasets[0].borderColor = window.stylingUtils && window.stylingUtils.getChartLineColor
                ? window.stylingUtils.getChartLineColor(0)
                : '#36A2EB';

            // Moon altitude
            window.journalChart.data.datasets[1].borderColor = window.stylingUtils && window.stylingUtils.getColor
                ? window.stylingUtils.getColor('--warning-color', '#FFC107')
                : '#FFC107';

            // Horizon
            window.journalChart.data.datasets[2].borderColor = window.stylingUtils && window.stylingUtils.getColor
                ? window.stylingUtils.getColor('--text-primary', '#333')
                : '#333';
        }

        // Update chart with new options
        window.journalChart.update('none');
    }

    // Register theme change callback
    if (window.stylingUtils && window.stylingUtils.onThemeChange) {
        window.stylingUtils.onThemeChange(function(event) {
            console.log('[journal_section.js] Theme changed to:', event.detail.theme);
            updateJournalChartForTheme();
        });

        // Initial update when stylingUtils is available
        // Wait a bit for chart to be initialized
        setTimeout(updateJournalChartForTheme, 100);
    }

    // ============================================
    // END THEME INTEGRATION
    // ============================================

    // Expose theme update function for external calls
    window.updateJournalChartForTheme = updateJournalChartForTheme;

})();
