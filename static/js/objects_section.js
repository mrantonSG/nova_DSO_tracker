(function() {
    'use strict';

    function normalizeObjectNameJS(name) {
        if (!name) return null;
        let nameStr = String(name).trim().toUpperCase();
        let match;

        // --- 1. Fix known "corrupt" or non-canonical inputs ---
        // This list now mirrors the final Python repair script and normalizer.

        // SH 2-155 -> SH2155 (Fix: SH2 + 1 or more digits)
        match = nameStr.match(/^(SH2)(\d+)$/);
        if (match) return `SH 2-${match[2]}`;

        // --- THIS IS NEW (Fix Bug 2) ---
        // SH 2-155 -> SH2-155 (Unifies dash-only format)
        match = nameStr.match(/^(SH2)-(\d+)$/);
        if (match) return `SH 2-${match[2]}`;
        // --- END OF NEW RULE ---

        // NGC 1976 -> NGC1976 (Fix: NGC + 1 or more digits)
        match = nameStr.match(/^(NGC)(\d+)$/);
        if (match) return `NGC ${match[2]}`;

        // VDB 1 -> VDB1
        match = nameStr.match(/^(VDB)(\d+)$/);
        if (match) return `VDB ${match[2]}`;

        // GUM 16 -> GUM16
        match = nameStr.match(/^(GUM)(\d+)$/);
        if (match) return `GUM ${match[2]}`;

        // TGU H1867 -> TGUH1867
        match = nameStr.match(/^(TGUH)(\d+)$/);
        if (match) return `TGU H${match[2]}`;

        // LHA 120-N 70 -> LHA120N70
        match = nameStr.match(/^(LHA)(\d+)(N)(\d+)$/); /* FIXED REGEX HERE */
        if (match) return `LHA ${match[2]}-N ${match[4]}`; /* FIXED REPLACEMENT HERE */

        // SNR G180.0-01.7 -> SNRG180.001.7
        match = nameStr.match(/^(SNRG)(\d+\.\d+)(\d+\.\d+)$/);
        if (match) return `SNR G${match[2]}-${match[3]}`;

        // CTA 1 -> CTA1
        match = nameStr.match(/^(CTA)(\d+)$/);
        if (match) return `CTA ${match[2]}`;

        // HB 3 -> HB3
        match = nameStr.match(/^(HB)(\d+)$/);
        if (match) return `HB ${match[2]}`;

        // PN ARO 121 -> PNARO121
        match = nameStr.match(/^(PNARO)(\d+)$/);
        if (match) return `PN ARO ${match[2]}`;

        // LIESTO 1 -> LIESTO1
        match = nameStr.match(/^(LIESTO)(\d+)$/);
        if (match) return `LIESTO ${match[2]}`;

        // PK 081-14.1 -> PK08114.1
        match = nameStr.match(/^(PK)(\d+)(\d{2}\.\d+)$/);
        if (match) return `PK ${match[2]}-${match[3]}`;

        // PN G093.3-02.4 -> PNG093.302.4
        match = nameStr.match(/^(PNG)(\d+\.\d+)(\d+\.\d+)$/);
        if (match) return `PN G${match[2]}-${match[3]}`;

        // WR 134 -> WR134
        match = nameStr.match(/^(WR)(\d+)$/);
        if (match) return `WR ${match[2]}`;

        // ABELL 21 -> ABELL21
        match = nameStr.match(/^(ABELL)(\d+)$/);
        if (match) return `ABELL ${match[2]}`;

        // BARNARD 33 -> BARNARD33
        match = nameStr.match(/^(BARNARD)(\d+)$/);
        if (match) return `BARNARD ${match[2]}`;

        // --- 2. Fix simple space removal (M, IC) ---
        // This rule handles user input like "M 42"

        // --- THIS IS CORRECTED (Fix Bug 1) ---
        match = nameStr.match(/^(M)\s+(.*)$/);
        if (match) {
            const prefix = match[1];
            // Remove all spaces from the number part
            const numberPart = match[2].replace(/\s+/g, '');
            return prefix + numberPart;
        }

        // --- 3. Default Fallback ---
        // For names that are already correct (e.g., "M42", "NGC 1976", "BARNARD 33")
        // just collapse multiple spaces into one.
        return nameStr.replace(/\s+/g, ' ');
    }

    // --- Sub-tab management ---
    function showObjectSubTab(tabName) {
        // Hide all content
        document.querySelectorAll('#objects-tab-content .detail-tab-content').forEach(tab => tab.classList.remove('active'));
        // Deactivate all buttons
        document.querySelectorAll('#objects-tab-content .detail-tab-button').forEach(button => button.classList.remove('active'));

        // Show selected content
        const contentEl = document.getElementById(tabName + '-object-subtab');
        if (contentEl) {
            contentEl.classList.add('active');
        }

        // Activate selected button
        const buttonEl = document.querySelector(`#objects-tab-content .detail-tab-button[data-tab="${tabName}"]`);
        if (buttonEl) {
            buttonEl.classList.add('active');
        }

        // Save choice
        localStorage.setItem('activeObjectSubTab', tabName);
        // When the 'manage' tab is shown, run the filter to update the count.
        if (tabName === 'manage' && typeof filterObjectsList === 'function') {
            filterObjectsList();
        }
    }

    // --- All moved/self-contained object JS ---
    function filterObjectsList() {
        // 1. Get filter values
        const filterId = document.getElementById('object-filter-id').value.toLowerCase();
        const filterName = document.getElementById('object-filter-name').value.toLowerCase();
        const filterCon = document.getElementById('object-filter-con').value.toLowerCase();
        const filterType = document.getElementById('object-filter-type').value.toLowerCase();
        const filterShared = document.getElementById('object-filter-shared').value;
        const filterSource = document.getElementById('object-filter-source').value.toLowerCase();

        // --- NEW: Get Notes Filter ---
        const filterNotes = document.getElementById('object-filter-notes').value.toLowerCase();

        // Numerical Filters
        const filterMagStr = document.getElementById('object-filter-mag').value;
        const filterSizeStr = document.getElementById('object-filter-size').value;
        const filterMag = filterMagStr ? parseFloat(filterMagStr) : null;
        const filterSize = filterSizeStr ? parseFloat(filterSizeStr) : null;

        // 2. Get all object blocks
        const objectBlocks = document.querySelectorAll('.objects-list .object-grid-container');

        // Counter Logic
        let visibleCount = 0;
        const totalCount = objectBlocks.length;

        // 3. Loop through each block and check
        objectBlocks.forEach(block => {
            let show = true;

            // Find the values within this block
            const objectIdEl = block.querySelector('.object-id strong');
            const nameInputEl = block.querySelector('input[id^="name_"]');
            const conInputEl = block.querySelector('input[id^="constellation_"]');
            const typeInputEl = block.querySelector('input[id^="type_"]');
            const sharedIndicator = block.querySelector('.shared-indicator');
            const sourceBadge = block.querySelector('.catalog-source-badge');

            // --- NEW: Find the Private Notes Hidden Input ---
            // Matches input IDs like "project_M42_hidden"
            const notesInput = block.querySelector('input[id^="project_"][id$="_hidden"]');

            const objectId = objectIdEl ? objectIdEl.textContent.toLowerCase() : '';
            const name = nameInputEl ? nameInputEl.value.toLowerCase() : '';
            const con = conInputEl ? conInputEl.value.toLowerCase() : '';
            const type = typeInputEl ? typeInputEl.value.toLowerCase() : '';

            // --- NEW: Get Notes Content ---
            // We search the value directly. Since Trix saves HTML, this will search the text
            // (and technically html tags, but that is usually fine for this purpose).
            const notesContent = notesInput ? notesInput.value.toLowerCase() : '';

            // Numerical Values
            const magInput = block.querySelector('input[id^="magnitude_"]');
            const sizeInput = block.querySelector('input[id^="size_"]');
            const magVal = magInput && magInput.value ? parseFloat(magInput.value) : 999;
            const sizeVal = sizeInput && sizeInput.value ? parseFloat(sizeInput.value) : 0;

            let sharedStatus = 'private';
            if (sharedIndicator) {
                const indicatorText = sharedIndicator.textContent.toLowerCase();
                if (indicatorText.includes('imported')) sharedStatus = 'imported';
                else if (indicatorText.includes('shared')) sharedStatus = 'shared';
            }
            const source = sourceBadge ? sourceBadge.textContent.toLowerCase() : '';

            const enabledInput = block.querySelector('input[name^="enabled_"]');
            const isEnabled = enabledInput ? enabledInput.value === 'on' : true;

            // 4. Apply filters
            if (filterId && !objectId.includes(filterId)) show = false;
            if (show && filterName && !name.includes(filterName)) show = false;
            if (show && filterCon && !con.includes(filterCon)) show = false;
            if (show && filterType && !type.includes(filterType)) show = false;

            // --- NEW: Check Notes ---
            if (show && filterNotes) {
                if (filterNotes === '*') {
                    // Wildcard: Show only if notes are NOT empty
                    if (!notesContent || notesContent.trim() === '') show = false;
                } else {
                    // Standard search
                    if (!notesContent.includes(filterNotes)) show = false;
                }
            }

            if (show && filterShared !== 'all') {
                if (filterShared === 'disabled') {
                    if (isEnabled) show = false;
                } else if (filterShared === 'enabled') {
                    if (!isEnabled) show = false;
                } else if (sharedStatus !== filterShared) {
                    show = false;
                }
            }

            if (show && filterSource) {
                if (filterSource.startsWith('=')) {
                    // Exact match: check if source equals the search term (minus the '=')
                    if (source !== filterSource.substring(1)) show = false;
                } else {
                    // Standard fuzzy match
                    if (!source.includes(filterSource)) show = false;
                }
            }
            if (show && filterMag !== null && magVal > filterMag) show = false;
            if (show && filterSize !== null && sizeVal < filterSize) show = false;

            // 5. Show or hide
            block.style.display = show ? '' : 'none';

            if (show) visibleCount++;
        });

        // Update Display
        const countDisplay = document.getElementById('object-count-display');
        if (countDisplay) {
            if (visibleCount === totalCount) {
                countDisplay.textContent = `Showing ${totalCount} objects`;
            } else {
                countDisplay.textContent = `Showing ${visibleCount} of ${totalCount} objects`;
            }
        }
    }

    function parseRAToDecimal(raStr) {
        raStr = String(raStr).trim();
        if (!isNaN(raStr) && !raStr.match(/[:\s]/)) {
            return parseFloat(raStr);
        }
        const parts = raStr.replace(/:/g, ' ').split(/\s+/).filter(Boolean);
        const h = parseFloat(parts[0]) || 0;
        const m = parseFloat(parts[1]) || 0;
        const s = parseFloat(parts[2]) || 0;
        return (h + m/60 + s/3600);
    }

    function parseDecToDecimal(decStr) {
        decStr = String(decStr).trim();
        if (!isNaN(decStr) && !decStr.match(/[:\s]/)) {
            return parseFloat(decStr);
        }
        const sign = decStr.startsWith('-') ? -1 : 1;
        const parts = decStr.replace(/^-|\+/, '').replace(/:/g, ' ').split(/\s+/).filter(Boolean);
        const d = parseFloat(parts[0]) || 0;
        const m = parseFloat(parts[1]) || 0;
        const s = parseFloat(parts[2]) || 0;
        return sign * (d + m/60 + s/3600);
    }

    // --- Bulk Action Functions ---

    function updateSelectionCount() {
        const count = document.querySelectorAll('.bulk-select-checkbox:checked').length;
        document.getElementById('selection-count').textContent = `${count} Selected`;
    }

    // Attach listener to checkbox changes dynamically
    document.addEventListener('change', function(e) {
        if (e.target.classList.contains('bulk-select-checkbox')) {
            updateSelectionCount();
        }
    });

    function selectAllVisibleObjects() {
        const objectBlocks = document.querySelectorAll('.objects-list .object-grid-container');
        objectBlocks.forEach(block => {
            if (block.style.display !== 'none') {
                const cb = block.querySelector('.bulk-select-checkbox');
                if (cb) cb.checked = true;
            }
        });
        updateSelectionCount();
    }

    function deselectAllObjects() {
        document.querySelectorAll('.bulk-select-checkbox').forEach(cb => cb.checked = false);
        updateSelectionCount();
    }

    function executeBulkAction(action) {
        const selectedCheckboxes = document.querySelectorAll('.bulk-select-checkbox:checked');
        const objectIds = Array.from(selectedCheckboxes).map(cb => cb.dataset.objectId);

        if (objectIds.length === 0) {
            alert("No objects selected.");
            return;
        }

        if (!confirm(`Are you sure you want to ${action.toUpperCase()} ${objectIds.length} objects?`)) {
            return;
        }

        fetch('/api/bulk_update_objects', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: action, object_ids: objectIds })
        })
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                alert(data.message);
                window.location.reload(); // Reload to reflect state changes and re-run jinja logic
            } else {
                alert('Error: ' + data.message);
            }
        })
        .catch(err => {
            console.error('Bulk action failed:', err);
            alert('Bulk action failed. See console.');
        });
    }

    // --- Duplicate Checker Logic ---

    function openDuplicateChecker() {
        document.getElementById('duplicates-modal').classList.add('is-visible');
        document.getElementById('duplicates-list').innerHTML = '';
        document.getElementById('duplicates-loading').style.display = 'block';

        fetch('/api/find_duplicates')
        .then(r => r.json())
        .then(data => {
            document.getElementById('duplicates-loading').style.display = 'none';
            const list = document.getElementById('duplicates-list');

            if (data.duplicates.length === 0) {
                list.innerHTML = '<p style="text-align: center; padding: 20px;">No potential duplicates found based on coordinates.</p>';
                return;
            }

            let html = '<table class="config-table"><thead><tr><th>Object A</th><th>Sep</th><th>Object B</th><th>Action</th></tr></thead><tbody>';

            data.duplicates.forEach((pair, idx) => {
                const rowId = `dup-row-${idx}`;
                const nameA = pair.object_a.Object;
                const nameB = pair.object_b.Object;

                html += `<tr id="${rowId}">
                    <td>
                        <strong>${nameA}</strong><br>
                        <small>${pair.object_a['Common Name'] || ''}</small><br>
                        <small class="muted-text">Src: ${pair.object_a.catalog_sources || 'Manual'}</small>
                    </td>
                    <td style="text-align: center; vertical-align: middle;">
                        ${pair.separation_arcmin}'
                    </td>
                    <td>
                        <strong>${nameB}</strong><br>
                        <small>${pair.object_b['Common Name'] || ''}</small><br>
                        <small class="muted-text">Src: ${pair.object_b.catalog_sources || 'Manual'}</small>
                    </td>
                    <td style="vertical-align: middle; text-align: center;">
                        <button class="action-button" style="font-size: 11px; margin-bottom: 5px;" onclick="mergeObjects('${nameA}', '${nameB}', '${rowId}')">Keep A, Merge B</button><br>
                        <button class="action-button" style="font-size: 11px;" onclick="mergeObjects('${nameB}', '${nameA}', '${rowId}')">Keep B, Merge A</button>
                    </td>
                </tr>`;
            });

            html += '</tbody></table>';
            list.innerHTML = html;
        })
        .catch(err => {
            console.error(err);
            document.getElementById('duplicates-loading').style.display = 'none';
            document.getElementById('duplicates-list').innerHTML = '<p style="color: red; text-align: center;">Error scanning for duplicates.</p>';
        });
    }

    function mergeObjects(keepId, mergeId, rowId) {
        if (!confirm(`Merge '${mergeId}' INTO '${keepId}'?\n\nThis will:\n1. Re-link journals/projects from ${mergeId} to ${keepId}\n2. Copy notes from ${mergeId}\n3. DELETE ${mergeId} permanently`)) {
            return;
        }

        fetch('/api/merge_objects', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ keep_id: keepId, merge_id: mergeId })
        })
        .then(r => r.json())
        .then(data => {
            if (data.status === 'success') {
                const row = document.getElementById(rowId);
                if (row) row.remove();
                if (document.querySelectorAll('#duplicates-list tr').length <= 1) {
                    document.getElementById('duplicates-list').innerHTML = '<p style="text-align: center; padding: 20px;">All duplicates resolved!</p>';
                }
            } else {
                alert('Error: ' + data.message);
            }
        })
        .catch(err => alert('Merge failed: ' + err));
    }

    function activateLazyTrix(container, inputId, placeholder) {
        // Prevent double initialization
        if (container.querySelector('trix-editor')) return;

        // Create the heavy editor element only now
        const editor = document.createElement('trix-editor');
        editor.setAttribute('input', inputId);
        editor.setAttribute('placeholder', placeholder);

        // Remove styling and interactivity from container to let Trix take over
        container.classList.remove('trix-lazy-box');
        container.removeAttribute('onclick');

        // Swap content
        container.innerHTML = '';
        container.appendChild(editor);

        // Optional: Focus the editor immediately
        // editor.focus();
    }

    function resetAddObjectForm() {
        const fieldsToClear = [
            'new_object', 'new_name', 'new_ra', 'new_dec', 'new_constellation',
            'new_type', 'new_magnitude', 'new_size', 'new_sb',
            'new_project_hidden', 'new_shared_notes_hidden',
            'new_image_url', 'new_image_credit', 'new_image_source_link',
            'new_description_text', 'new_description_credit', 'new_description_source_link'
        ];

        fieldsToClear.forEach(id => {
            const el = document.getElementById(id);
            if (el) {
                el.value = '';
                el.readOnly = false; // Ensure all are unlocked
            }
        });

        // --- NEW: Reset new_is_active checkbox ---
        const newIsActive = document.getElementById('new_is_active');
        if (newIsActive) newIsActive.checked = true; // Default to checked
        // --- END NEW ---

        const newProjectEditor = document.getElementById('new_project_editor');
        if (newProjectEditor) newProjectEditor.editor.loadHTML('');

        const newSharedNotesEditor = document.getElementById('new_shared_notes_editor');
        if (newSharedNotesEditor) newSharedNotesEditor.editor.loadHTML('');

        const newIsShared = document.getElementById('new_is_shared');
        if (newIsShared) newIsShared.checked = false;

        document.getElementById('object_result').innerHTML = '';

        document.getElementById('submit_new_object').style.display = 'inline-block';
        document.getElementById('confirm_add_object').style.display = 'none';
        document.getElementById('edit_object').style.display = 'none';
        document.getElementById('cancel_add_object').style.display = 'none';
    }

    // --- Event Listeners (run once the DOM is loaded) ---
    // We wrap this in a check in case the script is loaded multiple times
    if (!window.objectScriptLoaded) {
        window.objectScriptLoaded = true;

        document.addEventListener('DOMContentLoaded', () => {
            // Attach event listeners to tab buttons
            const tabButtons = document.querySelectorAll('#objects-tab-content .detail-tab-button[data-tab]');
            tabButtons.forEach(button => {
                button.addEventListener('click', function(e) {
                    // Don't trigger if clicking on help badge
                    if (e.target.classList.contains('help-badge')) {
                        return;
                    }
                    const tabName = this.getAttribute('data-tab');
                    if (tabName) {
                        showObjectSubTab(tabName);
                    }
                });
            });

            // Restore last active sub-tab
            const lastSubTab = localStorage.getItem('activeObjectSubTab');
            if (lastSubTab) {
                // Use requestAnimationFrame to ensure the function exists
                // when this script (inside an include) is parsed.
                requestAnimationFrame(() => {
                    // Check if function exists before calling
                    if (typeof showObjectSubTab === 'function') {
                        showObjectSubTab(lastSubTab);
                    } else {
                        console.warn('showObjectSubTab not defined yet, defaulting to first tab.');
                    }
                });
            }

            // --- Add Object Form Logic ---
            const resultDiv = document.getElementById('object_result');
            const submitNewObjectBtn = document.getElementById('submit_new_object');
            const confirmAddObjectBtn = document.getElementById('confirm_add_object');
            const editObjectBtn = document.getElementById('edit_object');
            const cancelAddObjectBtn = document.getElementById('cancel_add_object');

            if (submitNewObjectBtn) {
                submitNewObjectBtn.addEventListener('click', e => {
                    e.preventDefault();
                    const objectNameInput = document.getElementById('new_object');
                    const objectName = objectNameInput.value.trim();
                    if (!objectName) {
                        alert("Please enter an object identifier.");
                        return;
                    }

                    // 1. Normalize the name, just as you requested
                    const normName = normalizeObjectNameJS(objectName);
                    resultDiv.innerHTML = '<p class="progress-message">Checking your local library for ' + normName + '...</p>';

                    // --- 2. NEW LOGIC: PATH A (Check Local DB First) ---
                    // It calls the /api/get_object_data/ endpoint with the *normalized* name
                    fetch(`/api/get_object_data/${normName}`)
                    .then(response => {
                        if (response.ok) {
                            return response.json(); // Object was found locally
                        }
                        // Object not found, throw an error to trigger Path B (SIMBAD)
                        throw new Error('Object not found in local library. Checking SIMBAD...');
                    })
                    .then(data => {
                        // --- PATH A: SUCCESS - Object Found Locally ---
                        resultDiv.innerHTML = `<p class="message">Object '${normName}' found in your library. Loading for edit.</p>`;

                        // Populate form with YOUR LOCAL data
                        objectNameInput.value = data.Object; // Use the canonical name
                        document.getElementById('new_name').value = data["Common Name"];
                        document.getElementById('new_ra').value = data["RA (hours)"];
                        document.getElementById('new_dec').value = data["DEC (degrees)"];
                        document.getElementById('new_constellation').value = data.Constellation || '';
                        document.getElementById('new_type').value = data.Type || '';
                        document.getElementById('new_magnitude').value = data.Magnitude || '';
                        document.getElementById('new_size').value = data.Size || '';
                        document.getElementById('new_sb').value = data.SB || '';

                        // --- NEW: Populate Active Project state ---
                        const isActive = data.ActiveProject || false;
                        document.getElementById('new_is_active').checked = isActive;
                        // --- END NEW ---

                        // Populate Trix editors with local notes
                        const projectEditor = document.getElementById('new_project_editor');
                        if (projectEditor) projectEditor.editor.loadHTML(data.Project || '');

                        // Multi-user mode fields (will be ignored if elements don't exist)
                        const sharedNotesEditor = document.getElementById('new_shared_notes_editor');
                        if (sharedNotesEditor) sharedNotesEditor.editor.loadHTML(data.shared_notes || '');

                        const isSharedCheck = document.getElementById('new_is_shared');
                        if (isSharedCheck) {
                            isSharedCheck.checked = data.is_shared || false;
                            isSharedCheck.disabled = !!data.original_user_id; // Disable if imported
                        }

                        // Set form state to "Edit" (not "Confirm Add")
                        ['new_object', 'new_name', 'new_ra', 'new_dec'].forEach(id => document.getElementById(id).readOnly = true);
                        ['confirm_add_object', 'edit_object', 'cancel_add_object'].forEach(id => {
                            if (document.getElementById(id)) document.getElementById(id).style.display = 'inline-block';
                        });
                        submitNewObjectBtn.style.display = 'none';

                    })
                    .catch(err => {
                        // --- 3. PATH B: FAILED - Object Not Local, Search SIMBAD ---
                        // This is the ORIGINAL search logic, running only if the local check fails
                        resultDiv.innerHTML = `<p class="progress-message">${err.message}</p>`;

                        fetch("/search_object", { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ object: objectName })})
                        .then(r => r.json()).then(data => {
                          if (data.status !== "success") throw new Error(data.message);

                          // Populate with SIMBAD data
                          document.getElementById('new_name').value = data.data["Common Name"];
                          document.getElementById('new_ra').value = data.data["RA (hours)"];
                          document.getElementById('new_dec').value = data.data["DEC (degrees)"];
                          document.getElementById('new_constellation').value = data.data["Constellation"] || '';
                          ['new_name', 'new_ra', 'new_dec'].forEach(id => document.getElementById(id).readOnly = true);

                          // --- NEW: Set Active Project state for SIMBAD data (default true) ---
                          document.getElementById('new_is_active').checked = true;
                          // --- END NEW ---

                          resultDiv.innerHTML = `<p class="message">Found: ${data.data["Common Name"]}. <span class="progress-message">Fetching details...</span></p>`;
                          return fetch("/fetch_object_details", { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ object: objectName }) });
                        })
                        .then(r => r.json()).then(extra => {
                          const commonName = document.getElementById('new_name').value;
                          resultDiv.innerHTML = `<p class="message">Found: ${commonName}. Details loaded from SIMBAD.</p>`;
                          if (extra.status === 'success') {
                              document.getElementById('new_type').value = extra.data.Type || '';
                              document.getElementById('new_magnitude').value = extra.data.Magnitude || '';
                              document.getElementById('new_size').value = extra.data.Size || '';
                              document.getElementById('new_sb').value = extra.data.SB || '';
                          }
                          // Set form state to "Confirm Add"
                          ['confirm_add_object', 'edit_object', 'cancel_add_object'].forEach(id => {
                              if (document.getElementById(id)) document.getElementById(id).style.display = 'inline-block';
                          });
                          submitNewObjectBtn.style.display = 'none';
                        })
                        .catch(simbadErr => {
                          // --- 4. PATH C: BOTH LOCAL AND SIMBAD FAILED ---
                          resultDiv.innerHTML = `<p class="error">Error: ${simbadErr.message}.<br>You can now add the object manually and click 'Confirm Add'.</p>`;
                          ['confirm_add_object', 'edit_object', 'cancel_add_object'].forEach(id => {
                              if (document.getElementById(id)) document.getElementById(id).style.display = 'inline-block';
                          });
                          submitNewObjectBtn.style.display = 'none';
                          // Unlock all fields for manual entry
                          ['new_name', 'new_ra', 'new_dec', 'new_constellation', 'new_type', 'new_magnitude', 'new_size', 'new_sb', 'new_project_hidden', 'new_object', 'new_shared_notes_hidden', 'new_is_shared', 'new_is_active'].forEach(id => {
                              const el = document.getElementById(id);
                              if(el) el.readOnly = false;
                          });
                        });
                    });
                });
            }
            if (confirmAddObjectBtn) {
                confirmAddObjectBtn.addEventListener('click', e => {
                    e.preventDefault();
                    const raInput = document.getElementById('new_ra').value;
                    const decInput = document.getElementById('new_dec').value;

                    let raDecimal = parseRAToDecimal(raInput);
                    const decDecimal = parseDecToDecimal(decInput);

                    // --- FORMAT CHECK: Detect Degrees vs Hours ---
                    if (raDecimal > 24.0) {
                        const correctedRA = raDecimal / 15.0;
                        const userConfirmed = confirm(
                            `Warning: RA value (${raDecimal.toFixed(2)}) is > 24, which implies Degrees.\n\n` +
                            `Do you want to automatically convert this to ${correctedRA.toFixed(4)} Hours?`
                        );

                        if (userConfirmed) {
                            raDecimal = correctedRA;
                            // Optional: Update the UI to reflect the fix
                            document.getElementById('new_ra').value = raDecimal.toFixed(5);
                        } else {
                            return; // Stop submission so user can fix manually
                        }
                    }
                    // ---------------------------------------------

                    const payload = {
                        object: document.getElementById('new_object').value,
                        name: document.getElementById('new_name').value,
                        ra: raDecimal,
                        dec: decDecimal,
                        project: document.getElementById('new_project_hidden').value,
                        type: document.getElementById('new_type').value,
                        magnitude: document.getElementById('new_magnitude').value,
                        size: document.getElementById('new_size').value,
                        constellation: document.getElementById('new_constellation').value,
                        sb: document.getElementById('new_sb').value,
                        shared_notes: document.getElementById('new_shared_notes_hidden') ? document.getElementById('new_shared_notes_hidden').value : '',
                        is_shared: document.getElementById('new_is_shared') ? document.getElementById('new_is_shared').checked : false,
                        // --- NEW: Include Active Project Status ---
                        is_active: document.getElementById('new_is_active') ? document.getElementById('new_is_active').checked : false,
                        // --- END NEW ---
                        // --- NEW: Inspiration Fields ---
                        image_url: document.getElementById('new_image_url').value,
                        image_credit: document.getElementById('new_image_credit').value,
                        image_source_link: document.getElementById('new_image_source_link').value,
                        description_text: document.getElementById('new_description_text').value,
                        description_credit: document.getElementById('new_description_credit').value,
                        description_source_link: document.getElementById('new_description_source_link').value
                    };

                    fetch("/confirm_object", { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
                    .then(r => r.json()).then(data => {
                        if (data.status === "success") { window.location.reload(); }
                        else { throw new Error(data.message); }
                    }).catch(err => { resultDiv.innerHTML = `<p class="error">Error: ${err.message}</p>`; });
                });
            }
            if (editObjectBtn) {
                editObjectBtn.addEventListener('click', e => {
                    e.preventDefault();
                    ['new_name', 'new_ra', 'new_dec', 'new_type', 'new_magnitude', 'new_size', 'new_sb', 'new_project_hidden', 'new_object', 'new_shared_notes_hidden', 'new_is_shared', 'new_is_active'].forEach(id => {
                        const el = document.getElementById(id);
                        if(el) el.readOnly = false;
                    });
                });
            }
            if (cancelAddObjectBtn) {
                cancelAddObjectBtn.addEventListener('click', e => {
                    e.preventDefault();
                    resetAddObjectForm();
                });
            }

            // (Old 'update-single-object-btn' listener removed; using 'saveObjectData' from config_form.html)
        });
    }

    function confirmCatalogImport(form) {
        const packName = form.getAttribute('data-pack-name') || 'Catalog';
        return confirm(
            "Importing '" + packName + "'...\n\n" +
            "This will update your library with data from the server:\n" +
            "• New objects from this pack will be added.\n" +
            "• Existing objects will be updated with the latest images/descriptions.\n" +
            "• Your personal Project Notes, Status, and Framings remain safe.\n\n" +
            "Do you want to proceed?"
        );
    }

    // Expose functions needed by HTML inline event handlers
    window.filterObjectsList = filterObjectsList;
    window.selectAllVisibleObjects = selectAllVisibleObjects;
    window.deselectAllObjects = deselectAllObjects;
    window.executeBulkAction = executeBulkAction;
    window.openDuplicateChecker = openDuplicateChecker;
    window.mergeObjects = mergeObjects;
    window.activateLazyTrix = activateLazyTrix;
    window.confirmCatalogImport = confirmCatalogImport;
})();
