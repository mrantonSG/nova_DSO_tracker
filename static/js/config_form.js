    // --- Global variables (defined in inline template script) ---
    // CURRENT_USERNAME, rigsData, rigsDataLoaded, rigSort are all defined in the inline script

    // --- DEBUG ---
    console.log('[CONFIG_FORM] External script loaded!');
    console.log('[CONFIG_FORM] window.NOVA_CONFIG_FORM:', typeof window.NOVA_CONFIG_FORM);

    // --- Rigs Tab Functions ---

    function populateComponentFormForEdit(type, id) {
        let componentListKey;
        if (type === 'telescope') componentListKey = 'telescopes';
        else if (type === 'camera') componentListKey = 'cameras';
        else if (type === 'reducer_extender') componentListKey = 'reducers_extenders';
        else return console.error("Unknown component type:", type);

        const component = rigsData.components[componentListKey].find(c => c.id == id);
        if (!component) return console.error("Could not find component with ID:", id);

        const formType = type === 'reducer_extender' ? 'reducer' : type;
        const form = document.getElementById(`form-${formType}`);
        form.action = window.NOVA_CONFIG_FORM.urls.updateComponent;
        form.querySelector('input[name="component_id"]').value = component.id;
        form.querySelector('input[name="name"]').value = component.name;

        if (type === 'telescope') {
            form.querySelector('input[name="aperture_mm"]').value = component.aperture_mm;
            form.querySelector('input[name="focal_length_mm"]').value = component.focal_length_mm;
        } else if (type === 'camera') {
            form.querySelector('input[name="sensor_width_mm"]').value = component.sensor_width_mm;
            form.querySelector('input[name="sensor_height_mm"]').value = component.sensor_height_mm;
            form.querySelector('input[name="pixel_size_um"]').value = component.pixel_size_um;
        } else if (type === 'reducer_extender') {
            form.querySelector('input[name="factor"]').value = component.factor;
        }

        // --- START: New Share Logic ---
        const shareCheckbox = form.querySelector('input[name="is_shared"]');
        if (shareCheckbox) {
            shareCheckbox.checked = component.is_shared || false;
            // Disable sharing if it's an imported item
            if (component.original_user_id) {
                shareCheckbox.disabled = true;
                shareCheckbox.checked = false; // Can't re-share an imported item
            } else {
                shareCheckbox.disabled = false;
            }
        }
        // --- END: New Share Logic ---

        form.querySelector('button[type="submit"]').textContent = 'Update Component';
        form.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }

    function populateRigFormForEdit(id) {
        console.log('[CONFIG_FORM] populateRigFormForEdit called with id:', id, typeof id);
        console.log('[CONFIG_FORM] rigsData:', typeof rigsData, rigsData);

        const rig = rigsData.rigs.find(r => r.rig_id == id);  // Use == instead of === for type coercion
        console.log('[CONFIG_FORM] Found rig:', rig);
        if (!rig) {
            console.error('[CONFIG_FORM] Rig not found!');
            return;
        }

        const form = document.getElementById('form-rig');
        console.log('[CONFIG_FORM] Form found:', form);
        form.querySelector('input[name="rig_id"]').value = rig.rig_id;
        form.querySelector('input[name="rig_name"]').value = rig.rig_name;
        form.querySelector('select[name="telescope_id"]').value = rig.telescope_id;
        form.querySelector('select[name="camera_id"]').value = rig.camera_id;
        form.querySelector('select[name="reducer_extender_id"]').value = rig.reducer_extender_id || '';
        form.querySelector('button[type="submit"]').textContent = 'Update Rig';
        form.scrollIntoView({ behavior: 'smooth', block: 'start' });
        console.log('[CONFIG_FORM] Form populated successfully');
    }

    function safeNum(v, fallback = null) {
        const n = Number(v);
        return Number.isFinite(n) ? n : fallback;
    }

    function compareBy(a, b, getter, dir = 'asc') {
        const av = getter(a), bv = getter(b);
        const aNull = (av === null || av === undefined);
        const bNull = (bv === null || bv === undefined);
        if (aNull && bNull) return 0;
        if (aNull) return 1;
        if (bNull) return -1;
        if (av < bv) return dir === 'asc' ? -1 : 1;
        if (av > bv) return dir === 'asc' ? 1 : -1;
        return 0;
    }

    function sortRigs(rigs, sortValue) {
        const [key, dir] = sortValue.split('-');
        const getterMap = {
            name: (r) => (r.rig_name || '').toLowerCase(),
            fl:   (r) => safeNum(r.effective_focal_length, null),
            fr:   (r) => safeNum(r.f_ratio, null),
            scale:(r) => safeNum(r.image_scale, null),
            fovw: (r) => safeNum(r.fov_w_arcmin, null),
            recent: (r) => {
                const ts = r.created_at || r.updated_at || '';
                const tnum = Date.parse(ts);
                return Number.isFinite(tnum) ? tnum : (r.rig_id || '').toString();
            }
        };
        const getter = getterMap[key] || getterMap.name;
        const rigsCopy = rigs.slice();
        rigsCopy.sort((a, b) => compareBy(a, b, getter, dir));
        return rigsCopy;
    }

    function fetchRigsData() {
        if (rigsDataLoaded) return;
        fetch('/get_rig_data')
            .then(response => response.json())
            .then(data => {
                rigsData = data;
                if (data.sort_preference && typeof data.sort_preference === 'string') {
                    rigSort = data.sort_preference;
                    localStorage.setItem('rigSort', rigSort);
                    const sortSelectEl = document.getElementById('rig-sort');
                    if (sortSelectEl) sortSelectEl.value = rigSort;
                }
                renderRigsUI();
                updateSamplingInfo();
                rigsDataLoaded = true;
            })
            .catch(error => console.error("Error fetching rigs data:", error));
    }

    function renderRigsUI() {
        console.log('[CONFIG_FORM] renderRigsUI called!');
        const data = rigsData;
        console.log('[CONFIG_FORM] rigsData structure:', typeof data, data);

        // Validate data structure
        if (!data || typeof data !== 'object') {
            console.error('[CONFIG_FORM] Invalid rigsData:', data);
            return;
        }
        if (!data.components || !Array.isArray(data.components.telescopes)) {
            console.error('[CONFIG_FORM] Invalid data.components:', data.components);
            return;
        }
        if (!data.rigs || !Array.isArray(data.rigs)) {
            console.error('[CONFIG_FORM] Invalid data.rigs:', data.rigs);
            return;
        }

        const { telescopes, cameras, reducers_extenders } = data.components;
        const { rigs } = data;
        const sortSelectEl = document.getElementById('rig-sort');
        const currentSort = sortSelectEl ? (sortSelectEl.value || rigSort) : rigSort;
        if (sortSelectEl && sortSelectEl.value !== currentSort) {
            sortSelectEl.value = currentSort;
        }
        const rigsSorted = sortRigs(rigs, currentSort);

        // --- Helper to create shared/imported indicator ---
        const createIndicator = (c) => {
            if (c.original_user_id) return ' <span class="shared-indicator" title="Imported item">Imported</span>';
            if (c.is_shared) return ' <span class="shared-indicator" title="You are sharing this item">Shared</span>';
            return '';
        };

        document.getElementById('telescope-list').innerHTML = telescopes.map(t => `
            <li>
                <div class="item-info">${t.name} (${t.aperture_mm}mm / ${t.focal_length_mm}mm)${createIndicator(t)}</div>
                <div class="item-actions">
                    <button type="button" class="edit-btn" onclick="populateComponentFormForEdit('telescope', '${t.id}')">Edit</button>
                    <form action="${window.NOVA_CONFIG_FORM.urls.deleteComponent}" method="post" onsubmit="return confirm('Deleting a component is permanent and cannot be undone. Are you sure?');">
                        <input type="hidden" name="component_id" value="${t.id}"><input type="hidden" name="component_type" value="telescopes">
                        <button type="submit" class="delete-btn">Delete</button>
                    </form>
                </div>
            </li>`).join('') || '<li>No telescopes defined.</li>';

        document.getElementById('camera-list').innerHTML = cameras.map(c => `
            <li>
                <div class="item-info">${c.name} (${c.pixel_size_um}μm pixel)${createIndicator(c)}</div>
                <div class="item-actions">
                    <button type="button" class="edit-btn" onclick="populateComponentFormForEdit('camera', '${c.id}')">Edit</button>
                    <form action="${window.NOVA_CONFIG_FORM.urls.deleteComponent}" method="post" onsubmit="return confirm('Deleting a component is permanent and cannot be undone. Are you sure?');">
                        <input type="hidden" name="component_id" value="${c.id}"><input type="hidden" name="component_type" value="cameras">
                        <button type="submit" class="delete-btn">Delete</button>
                    </form>
                </div>
            </li>`).join('') || '<li>No cameras defined.</li>';

        document.getElementById('reducer-list').innerHTML = reducers_extenders.map(r => `
            <li>
                <div class="item-info">${r.name} (${r.factor}x)${createIndicator(r)}</div>
                <div class="item-actions">
                    <button type="button" class="edit-btn" onclick="populateComponentFormForEdit('reducer_extender', '${r.id}')">Edit</button>
                    <form action="${window.NOVA_CONFIG_FORM.urls.deleteComponent}" method="post" onsubmit="return confirm('Deleting a component is permanent and cannot be undone. Are you sure?');">
                        <input type="hidden" name="component_id" value="${r.id}"><input type="hidden" name="component_type" value="reducers_extenders">
                        <button type="submit" class="delete-btn">Delete</button>
                    </form>
                </div>
            </li>`).join('') || '<li>No reducers defined.</li>';

        const teleSelect = document.getElementById('tele_select');
        const camSelect = document.getElementById('cam_select');
        const redSelect = document.getElementById('red_select');
        if(teleSelect) teleSelect.innerHTML = '<option value="" disabled selected>-- Select a Telescope --</option>' + telescopes.map(t => `<option value="${t.id}">${t.name}</option>`).join('');
        if(camSelect) camSelect.innerHTML = '<option value="" disabled selected>-- Select a Camera --</option>' + cameras.map(c => `<option value="${c.id}">${c.name}</option>`).join('');
        if(redSelect) redSelect.innerHTML = '<option value="">-- None --</option>' + reducers_extenders.map(r => `<option value="${r.id}">${r.name} (${r.factor}x)</option>`).join('');

        const rigList = document.getElementById('existing-rigs-list');
        rigList.innerHTML = rigsSorted.map(rig => {
            const tele = telescopes.find(t => t.id === rig.telescope_id);
            const cam = cameras.find(c => c.id === rig.camera_id);
            const red = reducers_extenders.find(r => r.id === rig.reducer_extender_id);
            let detailsHtml = `<strong>${rig.rig_name}</strong><br><small>Telescope: ${tele ? tele.name : 'N/A'}<br>Camera: ${cam ? cam.name : 'N/A'}<br>${red ? `Reducer/Extender: ${red.name}` : ''}</small>`;
            if (rig.image_scale) {
                detailsHtml += `<hr style="margin: 0.5em 0; border-color: ${(window.stylingUtils && window.stylingUtils.getColor) ? window.stylingUtils.getColor('--border-light', '#f5f5f5') : '#f5f5f5'};"><small style="color: ${(window.stylingUtils && window.stylingUtils.getColor) ? window.stylingUtils.getColor('--text-secondary', '#666') : '#666'};">Effective FL: ${rig.effective_focal_length.toFixed(0)} mm (f/${rig.f_ratio.toFixed(1)})<br>Image Scale: ${rig.image_scale.toFixed(2)} arcsec/pixel<br>Field of View: ${rig.fov_w_arcmin.toFixed(1)}' x ${rig.fov_h_arcmin.toFixed(1)}'</small>`;
            }
            return `<li data-rig-id="${rig.rig_id}">
                        <div class="item-info">${detailsHtml}</div>
                        <div class="item-actions">
                            <button type="button" class="edit-btn" onclick="populateRigFormForEdit('${rig.rig_id}')">Edit</button>
                            <form action="${window.NOVA_CONFIG_FORM.urls.deleteRig}" method="post" onsubmit="return confirm('Are you sure you want to delete the rig \\'${rig.rig_name}\\'?');">
                                <input type="hidden" name="rig_id" value="${rig.rig_id}">
                                <button type="submit" class="delete-btn">Delete</button>
                            </form>
                        </div>
                    </li>`;
        console.log('[CONFIG_FORM] Generated Edit button for rig:', rig.rig_name, 'id:', rig.rig_id);
        }).join('') || '<li>No rigs configured yet.</li>';

        document.getElementById('tele-count').innerText = telescopes.length;
        document.getElementById('cam-count').innerText = cameras.length;
        document.getElementById('red-count').innerText = reducers_extenders.length;
        document.getElementById('rig-count').innerText = rigs.length;
    }

    function updateSamplingInfo() {
        const seeingSelect = document.getElementById('seeing-select');
        const selectedSeeing = seeingSelect.value;
        const rigListItems = document.querySelectorAll('#existing-rigs-list li');

        rigListItems.forEach(item => {
            const infoContainer = item.querySelector('.item-info');
            infoContainer.querySelectorAll('.sampling-info, .sampling-binning-tip').forEach(el => el.remove());
            if (selectedSeeing === 'none') return;

            const rigId = item.dataset.rigId;
            const rigIdNum = parseInt(rigId, 10);
            const rig = rigsData.rigs.find(r => r.rig_id === rigIdNum);
            if (rig && rig.image_scale) {
                const imageScale = rig.image_scale;
                const [seeingLow, seeingHigh] = selectedSeeing.split('-').map(parseFloat);
                const seeingAvg = (seeingLow + seeingHigh) / 2;
                const samplingAvg = seeingAvg / imageScale;

                let text = '', colorClass = '', isOversampled = false;
                if (samplingAvg > 4.0) { text = `Oversampled: ${samplingAvg.toFixed(1)} px/FWHM`; colorClass = 'sampling-oversampled'; isOversampled = true; }
                else if (samplingAvg > 3.0) { text = `Slightly Oversampled: ${samplingAvg.toFixed(1)} px/FWHM`; colorClass = 'sampling-slightly-oversampled'; isOversampled = true; }
                else if (samplingAvg >= 1.0) { text = `Good Sampling: ${samplingAvg.toFixed(1)} px/FWHM`; colorClass = 'sampling-good'; }
                else if (samplingAvg >= 0.67) { text = `Slightly Undersampled: ${samplingAvg.toFixed(1)} px/FWHM`; colorClass = 'sampling-slightly-undersampled'; }
                else { text = `Undersampled: ${samplingAvg.toFixed(1)} px/FWHM`; colorClass = 'sampling-undersampled'; }

                const infoEl = document.createElement('span');
                infoEl.className = `sampling-info ${colorClass}`;
                infoEl.textContent = text;
                infoContainer.appendChild(infoEl);

                if (isOversampled) {
                    const binningEl = document.createElement('small');
                    binningEl.className = 'sampling-binning-tip';
                    const binnedScale = (imageScale * 2).toFixed(2);
                    const binnedSampling = (samplingAvg / 2).toFixed(1);
                    binningEl.textContent = `Tip: 2x2 binning would yield ~${binnedScale}"/px (${binnedSampling} px/FWHM)`;
                    infoContainer.appendChild(binningEl);
                }
            }
        });
    }
    function fetchSharedItems() {
        fetch('/api/get_shared_items')
            .then(response => response.json())
            .then(data => {
                const objectsBody = document.getElementById('shared-objects-body');
                const componentsBody = document.getElementById('shared-components-body');
                const viewsBody = document.getElementById('shared-views-body');

                const importedObjectIds = new Set(data.imported_object_ids || []);
                const importedComponentIds = new Set(data.imported_component_ids || []);
                const importedViewIds = new Set(data.imported_view_ids || []);

                // --- Render Shared Objects ---
                if (data.objects && data.objects.length > 0) {
                    objectsBody.innerHTML = data.objects.map(obj => {
                        const isImported = importedObjectIds.has(obj.id);
                        const isOwner = (obj.shared_by_user === CURRENT_USERNAME);
                        const status = isImported ? 'imported' : 'unimported';

                        let actionButton = '';
                        if (isOwner) {
                            actionButton = `<button class="imported-button" disabled style="background-color:${(window.stylingUtils && window.stylingUtils.getColor) ? window.stylingUtils.getColor('--border-medium-alt', '#ccc') : '#ccc'}; color:${(window.stylingUtils && window.stylingUtils.getColor) ? window.stylingUtils.getColor('--text-secondary', '#555') : '#555'}; cursor:default;">Owner</button>`;
                        } else if (isImported) {
                            actionButton = `<button class="imported-button" disabled>Imported</button>`;
                        } else {
                            actionButton = `<button class="action-button import-button" onclick="importSharedItem(${obj.id}, 'object', this)">Import</button>`;
                        }

                        const dataAttrs = `
                            data-id="${obj.id}"
                            data-status="${status}"
                            data-object_name="${obj.object_name}"
                            data-common_name="${obj.common_name}"
                            data-type="${obj.type || ''}"
                            data-constellation="${obj.constellation || ''}"
                            data-shared_by_user="${obj.shared_by_user}"
                        `;

                        const imgHtml = obj.image_url
                            ? `<img src="${obj.image_url}" style="width: 34px; height: 34px; object-fit: cover; border-radius: 3px; vertical-align: middle; border: 1px solid ${(window.stylingUtils && window.stylingUtils.getColor) ? window.stylingUtils.getColor('--border-medium', '#ddd') : '#ddd'};" title="Has image">`
                            : '';

                        return `
                        <tr ${dataAttrs}>
                            <td style="text-align: center; padding: 4px;">${imgHtml}</td>
                            <td><strong>${obj.object_name}</strong></td>
                            <td>${obj.common_name}</td>
                            <td>${obj.type || 'N/A'}</td>
                            <td>${obj.constellation || 'N/A'}</td>
                            <td>${obj.shared_by_user}</td>
                            <td class="notes-cell">
                                <button class="action-button notes-button"
                                        data-notes="${(obj.shared_notes || '').replace(/"/g, '&quot;')}"
                                        onclick="showSharedNotes('${obj.object_name}', this.dataset.notes)">
                                    View
                                </button>
                            </td>
                            <td class="action-cell">${actionButton}</td>
                        </tr>`;
                    }).join('');
                } else {
                    objectsBody.innerHTML = '<tr><td colspan="8" style="text-align: center; padding: 20px; color: ' + ((window.stylingUtils && window.stylingUtils.getColor) ? window.stylingUtils.getColor('--text-secondary', '#555') : '#555') + ';">No shared objects found from other users.</td></tr>';
                }

                // --- Render Shared Components ---
                if (data.components && data.components.length > 0) {
                    componentsBody.innerHTML = data.components.map(comp => {
                        const isImported = importedComponentIds.has(comp.id);
                        const isOwner = (comp.shared_by_user === CURRENT_USERNAME);
                        const status = isImported ? 'imported' : 'unimported';

                        let actionButton = '';
                        if (isOwner) {
                            actionButton = `<button class="imported-button" disabled style="background-color:${(window.stylingUtils && window.stylingUtils.getColor) ? window.stylingUtils.getColor('--border-medium-alt', '#ccc') : '#ccc'}; color:${(window.stylingUtils && window.stylingUtils.getColor) ? window.stylingUtils.getColor('--text-secondary', '#555') : '#555'}; cursor:default;">Owner</button>`;
                        } else if (isImported) {
                            actionButton = `<button class="imported-button" disabled>Imported</button>`;
                        } else {
                            actionButton = `<button class="action-button import-button" onclick="importSharedItem(${comp.id}, 'component', this)">Import</button>`;
                        }

                        const dataAttrs = `
                            data-id="${comp.id}"
                            data-status="${status}"
                            data-name="${comp.name}"
                            data-kind="${comp.kind}"
                            data-shared_by_user="${comp.shared_by_user}"
                        `;

                        return `
                        <tr ${dataAttrs}>
                            <td><strong>${comp.name}</strong></td>
                            <td>${comp.kind}</td>
                            <td>${comp.shared_by_user}</td>
                            <td class="action-cell">${actionButton}</td>
                        </tr>`;
                    }).join('');
                } else {
                    componentsBody.innerHTML = '<tr><td colspan="4" style="text-align: center; padding: 20px; color: ' + ((window.stylingUtils && window.stylingUtils.getColor) ? window.stylingUtils.getColor('--text-secondary', '#555') : '#555') + ';">No shared components found from other users.</td></tr>';
                }

                // --- Render Shared Views ---
                if (data.views && data.views.length > 0) {
                    viewsBody.innerHTML = data.views.map(view => {
                        const isImported = importedViewIds.has(view.id);
                        const isOwner = (view.shared_by_user === CURRENT_USERNAME);
                        const status = isImported ? 'imported' : 'unimported';

                        let actionButton = '';
                        if (isOwner) {
                            actionButton = `<button class="imported-button" disabled style="background-color:${(window.stylingUtils && window.stylingUtils.getColor) ? window.stylingUtils.getColor('--border-medium-alt', '#ccc') : '#ccc'}; color:${(window.stylingUtils && window.stylingUtils.getColor) ? window.stylingUtils.getColor('--text-secondary', '#555') : '#555'}; cursor:default;">Owner</button>`;
                        } else if (isImported) {
                            actionButton = `<button class="imported-button" disabled>Imported</button>`;
                        } else {
                            actionButton = `<button class="action-button import-button" onclick="importSharedItem(${view.id}, 'view', this)">Import</button>`;
                        }

                        const dataAttrs = `
                            data-id="${view.id}"
                            data-status="${status}"
                            data-name="${view.name}"
                            data-description="${view.description || ''}"
                            data-shared_by_user="${view.shared_by_user}"
                        `;

                        return `
                        <tr ${dataAttrs}>
                            <td><strong>${view.name}</strong></td>
                            <td>${view.description || '-'}</td>
                            <td>${view.shared_by_user}</td>
                            <td class="action-cell">${actionButton}</td>
                        </tr>`;
                    }).join('');
                } else {
                    viewsBody.innerHTML = '<tr><td colspan="4" style="text-align: center; padding: 20px; color: ' + ((window.stylingUtils && window.stylingUtils.getColor) ? window.stylingUtils.getColor('--text-secondary', '#555') : '#555') + ';">No shared views found from other users.</td></tr>';
                }
            })
            .catch(error => {
                console.error("Error fetching shared items:", error);
                document.getElementById('shared-objects-body').innerHTML = '<tr><td colspan="8" style="text-align: center; color: ' + ((window.stylingUtils && window.stylingUtils.getDangerColor) ? window.stylingUtils.getDangerColor() : 'red') + ';">Error loading shared items.</td></tr>';
                document.getElementById('shared-components-body').innerHTML = '<tr><td colspan="4" style="text-align: center; color: ' + ((window.stylingUtils && window.stylingUtils.getDangerColor) ? window.stylingUtils.getDangerColor() : 'red') + ';">Error loading shared items.</td></tr>';
                document.getElementById('shared-views-body').innerHTML = '<tr><td colspan="4" style="text-align: center; color: ' + ((window.stylingUtils && window.stylingUtils.getDangerColor) ? window.stylingUtils.getDangerColor() : 'red') + ';">Error loading shared items.</td></tr>';
            });
    }

    function importSharedItem(itemId, itemType, button) {
        if (!confirm(`Are you sure you want to import this ${itemType}?`)) {
            return;
        }

        button.disabled = true;
        button.textContent = 'Importing...';

        fetch('/api/import_item', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: itemId, type: itemType })
        })
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                alert(data.message);
                button.textContent = 'Imported';
                button.className = 'imported-button'; // Change class

                // Update the row's status for filtering
                const row = button.closest('tr');
                if(row) row.dataset.status = 'imported';
            } else {
                alert(`Error: ${data.message}`);
                button.disabled = false;
                button.textContent = 'Import';
            }
        })
        .catch(error => {
            console.error('Import failed:', error);
            alert('Import failed. See console for details.');
            button.disabled = false;
            button.textContent = 'Import';
        });
    }

    function showSharedNotes(objectName, notesHtml) {
        document.getElementById('notes-modal-title').textContent = `Shared Notes for ${objectName}`;
        document.getElementById('notes-modal-content').innerHTML = notesHtml; // Notes are pre-sanitized by the backend
        document.getElementById('notes-modal').classList.add('is-visible');
    }

    function closeNotesModal() {
        document.getElementById('notes-modal').classList.remove('is-visible');
    }

    function filterSharedTables() {
        ['objects', 'components', 'views'].forEach(type => {
            const tableId = `shared-${type}-table`;
            const bodyId = `shared-${type}-body`;
            const table = document.getElementById(tableId);
            const body = document.getElementById(bodyId);
            if (!table || !body) return;

            // 1. Get all filter values from the header
            const filters = {};
            table.querySelectorAll('.filter-row [data-col]').forEach(input => {
                filters[input.dataset.col] = input.value.toLowerCase();
            });

            // 2. Loop through all data rows in the body
            body.querySelectorAll('tr[data-id]').forEach(row => {
                let show = true;

                // Check each filter against the row's data
                for (const col in filters) {
                    const filterVal = filters[col];
                    if (filterVal === 'all') continue; // Skip 'all' dropdowns

                    const rowVal = (row.dataset[col] || '').toLowerCase();

                    if (col === 'status') {
                        if (filterVal === 'imported' && rowVal !== 'imported') show = false;
                        if (filterVal === 'unimported' && rowVal !== 'unimported') show = false;
                    } else {
                        if (!rowVal.includes(filterVal)) {
                            show = false;
                        }
                    }
                    if (!show) break; // Stop checking if one filter fails
                }

                row.style.display = show ? '' : 'none';
            });
        });
    }

    function confirmAndFetchDetails(formElement) {
        if (!confirm("This will scan all your objects and fetch missing details (Type, Magnitude, Size, etc.) from external databases.\n\nDepending on your library size, this may take a few moments.\n\nProceed?")) {
            return;
        }

        const button = document.getElementById('fetch-details-button');
        const modal = document.getElementById('fetch-progress-modal');
        const bar = document.getElementById('fetch-progress-bar');
        const text = document.getElementById('fetch-progress-text');

        if (button) { button.disabled = true; button.innerText = 'Connecting...'; }
        if (modal) modal.style.display = 'flex';

        // Reset bar visual state for manual control (disable CSS animation for width)
        if (bar) {
            bar.style.width = '0%';
            bar.classList.add('active'); // Keep stripes
            bar.style.animation = 'progress-stripes 1s linear infinite'; // Only stripes, no growth
        }

        const evtSource = new EventSource(window.NOVA_CONFIG_FORM.urls.streamFetchDetails);

        evtSource.onmessage = function(e) {
            try {
                const data = JSON.parse(e.data);

                if (data.error) {
                    text.innerText = "Error: " + data.error;
                    text.style.color = "red";
                    evtSource.close();
                    if(button) { button.disabled = false; button.innerText = 'Retry'; }
                    return;
                }

                // Update UI
                if (data.progress !== undefined && bar) {
                    bar.style.width = data.progress + '%';
                }
                if (data.message && text) {
                    text.innerText = data.message;
                }

                // Completion
                if (data.done) {
                    evtSource.close();
                    setTimeout(() => {
                        window.location.reload();
                    }, 800);
                }
            } catch (err) {
                console.error("Stream parse error:", err);
            }
        };

        evtSource.onerror = function(err) {
            console.error("EventSource failed:", err);
            evtSource.close();
            if (text) {
                text.innerText = "Connection lost. Refreshing page...";
            }
            setTimeout(() => window.location.reload(), 2000);
        };
    }
    // --- NEW Import Handler (replaces setupImport) ---
        function handleImportSubmit(fileInput, entityName) {
            if (!fileInput.files[0]) return; // No file selected

            let confirmMsg = `This will import the ${entityName}. Are you sure?`;
            if (entityName === 'configuration' || entityName === 'journal' || entityName === 'rigs file') {
                confirmMsg = `This will merge/overwrite your current ${entityName}. The existing data will be backed up. Are you sure?`;
            }

            if (confirm(confirmMsg)) {
                // User confirmed, submit via fetch
                const formData = new FormData();
                formData.append("file", fileInput.files[0]);
                const url = fileInput.form.action; // Get URL from the form's action attribute

                // 1. Show a "please wait" message
                const msgContainer = document.getElementById('flash-message-container');
                if (msgContainer) msgContainer.innerHTML = `<div class="flash-message" style="padding: 12px 20px; border-radius: 6px; color: white; font-weight: bold; background-color: ${(window.stylingUtils && window.stylingUtils.getColor) ? window.stylingUtils.getColor('--info-color-alt2', '#007bff') : '#007bff'}; margin-bottom: 10px; box-shadow: 0 4px 12px rgba(0,0,0,0.15);">Importing ${entityName}, please wait...</div>`;

                fetch(url, {
                    method: "POST",
                    body: formData,
                    redirect: 'manual'  // <-- CRITICAL: Do NOT follow the redirect
                })
                .then(resp => {
                    // resp.type will be 'opaqueredirect' if the server-side
                    // (Python) finished and sent a redirect(). This is SUCCESS.
                    // resp.ok will be true if the server just sent 200 OK (no redirect).
                    // resp.ok will be false if the server sent 400/500 (an error).

                    if (resp.ok || resp.type === 'opaqueredirect') {
                        // SUCCESS! The server has set the flash message.
                        // Now we reload the page to see it.
                        window.location.reload();
                    } else {
                        // This handles server errors (4xx, 5xx)
                        throw new Error(`Import failed: Server returned status ${resp.status}`);
                    }
                })
                .catch(err => {
                    // This catches network failures or the error thrown above
                    alert(`${entityName} import failed: ${err.message}`);
                    if (msgContainer) msgContainer.innerHTML = '';
                    fileInput.value = ""; // Clear on failure
                });

            } else {
                // User cancelled, clear the file input so they can try again
                fileInput.value = "";
            }
        }
    // --- Image Upload Logic ---
    function uploadObjectImage(fileInput, targetInputId) {
        if (!fileInput.files[0]) return;

        const formData = new FormData();
        formData.append("file", fileInput.files[0]);

        const btn = fileInput.previousElementSibling; // The upload button
        const originalText = btn.textContent;
        btn.textContent = "...";
        btn.disabled = true;

        fetch(window.NOVA_CONFIG_FORM.urls.uploadEditorImage, {
            method: "POST",
            body: formData
        })
        .then(response => response.json())
        .then(data => {
            if (data.url) {
                document.getElementById(targetInputId).value = data.url;
                btn.textContent = "Done";
                setTimeout(() => { btn.textContent = originalText; btn.disabled = false; }, 1500);
            } else {
                alert("Upload error: " + (data.error || "Unknown error"));
                btn.textContent = originalText;
                btn.disabled = false;
            }
        })
        .catch(err => {
            console.error("Upload failed:", err);
            alert("Upload failed. See console.");
            btn.textContent = originalText;
            btn.disabled = false;
        });

        fileInput.value = ""; // Reset
    }

    // --- Save Logic for Objects (API) ---
    function saveObjectData(btn, objectName) {
        const originalText = btn.textContent;
        btn.textContent = "Saving...";
        btn.disabled = true;

        // Helper to safely get value by ID
        const getVal = (id) => {
            const el = document.getElementById(id);
            return el ? (el.type === 'checkbox' ? el.checked : el.value) : "";
        };

        // Gather ALL fields to prevent overwriting with nulls
        // Note: IDs match the standard naming convention (e.g. ra_M31)
        const payload = {
            object_id: objectName,
            name: getVal(`name_${objectName}`),
            ra: getVal(`ra_${objectName}`),
            dec: getVal(`dec_${objectName}`),
            constellation: getVal(`constellation_${objectName}`),
            type: getVal(`type_${objectName}`),
            magnitude: getVal(`magnitude_${objectName}`),
            size: getVal(`size_${objectName}`),
            sb: getVal(`sb_${objectName}`),
            is_active: getVal(`active_project_${objectName}`),
            project_notes: getVal(`project_${objectName}`), // Trix updates hidden input
            is_shared: getVal(`is_shared_${objectName}`),
            shared_notes: getVal(`shared_notes_${objectName}`),

            // --- NEW INSPIRATION FIELDS ---
            image_url: getVal(`image_url_${objectName}`),
            image_credit: getVal(`image_credit_${objectName}`),
            image_source_link: getVal(`image_source_link_${objectName}`), // Ensure image link is saved
            description_text: getVal(`description_text_${objectName}`),
            description_credit: getVal(`description_credit_${objectName}`), // Ensure text credit is saved
            description_source_link: getVal(`description_source_link_${objectName}`) // Ensure text link is saved
        };

        fetch('/api/update_object', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        })
        .then(response => response.json())
        .then(data => {
            if (data.status === 'success') {
                // Flash success button state
                btn.textContent = "Saved!";
                btn.style.backgroundColor = (window.stylingUtils && window.stylingUtils.getSuccessColor) ? window.stylingUtils.getSuccessColor() : "#28a745";
                setTimeout(() => {
                    btn.textContent = originalText;
                    btn.style.backgroundColor = ""; // Reset to default
                    btn.disabled = false;
                }, 2000);
            } else {
                alert("Error saving: " + data.message);
                btn.textContent = originalText;
                btn.disabled = false;
            }
        })
        .catch(err => {
            console.error(err);
            alert("Network error saving object.");
            btn.textContent = originalText;
            btn.disabled = false;
        });
    }
    // --- Main Initializer ---
    document.addEventListener('DOMContentLoaded', () => {
        // Double-initialization guard
        if (window.novaState && window.novaState.data && window.novaState.data.configFormInitialized) {
            console.log('[CONFIG_FORM] Already initialized, skipping...');
            return;
        }

        // Mark as initialized
        if (window.novaState && window.novaState.data) {
            window.novaState.data.configFormInitialized = true;
        }

        let locationDropdownsInitialized = false; // Flag to check if dropdowns are init

        // --- NEW: Location Limit & Counter Logic ---
        const MAX_ACTIVE_LOCATIONS = 5;

        function setupCharCounters() {
            document.querySelectorAll('textarea[maxlength][data-counter-id]').forEach(textarea => {
                const counterId = textarea.dataset.counterId;
                const counterEl = document.getElementById(counterId);
                if (counterEl) {
                    const updateCounter = () => { counterEl.textContent = `${textarea.value.length} / ${textarea.maxLength}`; };
                    textarea.addEventListener('input', updateCounter);
                    updateCounter();
                }
            });
        }

        function manageActiveLocationLimit() {
            const checkboxes = document.querySelectorAll('.locations-list .active-location-checkbox');
            const checkedCount = document.querySelectorAll('.locations-list .active-location-checkbox:checked').length;
            const counterEl = document.getElementById('active-location-counter');

            if(counterEl) {
                counterEl.textContent = `(${checkedCount} / ${MAX_ACTIVE_LOCATIONS} active)`;
                if (checkedCount >= MAX_ACTIVE_LOCATIONS) {
                    counterEl.style.color = (window.stylingUtils && window.stylingUtils.getDangerColor) ? window.stylingUtils.getDangerColor() : '#dc3545';
                    counterEl.style.fontWeight = 'bold';
                } else {
                    counterEl.style.color = (window.stylingUtils && window.stylingUtils.getColor) ? window.stylingUtils.getColor('--text-secondary', '#555') : '#555';
                    counterEl.style.fontWeight = 'normal';
                }
            }

            if (checkedCount >= MAX_ACTIVE_LOCATIONS) {
                checkboxes.forEach(cb => {
                    if (!cb.checked) {
                        cb.disabled = true;
                        cb.closest('div').style.opacity = '0.5';
                    }
                });
            } else {
                checkboxes.forEach(cb => {
                    cb.disabled = false;
                    cb.closest('div').style.opacity = '1';
                });
            }
        }
        // --- END of new logic block ---

        // --- Anonymous telemetry client ping (once per 24h) ---
        try {
          const TELEMETRY_KEY = 'novaTelemetryPingAt', lastPing = parseInt(localStorage.getItem(TELEMETRY_KEY) || '0', 10), now = Date.now(), DAY_MS = 86400000;
          const telemetryEnabled = window.NOVA_CONFIG_FORM.telemetryEnabled;
          if (telemetryEnabled && (now - lastPing) > DAY_MS) {
            fetch('/telemetry/ping', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ browser_user_agent: navigator.userAgent })})
              .then(() => localStorage.setItem(TELEMETRY_KEY, String(now))).catch(()=>{});
          }
        } catch (e) {}

        // --- Tab Management ---
        const tabs = document.querySelectorAll('.tab-button');
        const contentPanels = {
            general: document.getElementById('general-tab-content'),
            locations: document.getElementById('locations-tab-content'),
            objects: document.getElementById('objects-tab-content'),
            rigs: document.getElementById('rigs-tab-content'),
            shared: document.getElementById('shared-tab-content')
        };
        let activeTab = localStorage.getItem('activeConfigTab') || 'general';

        // ---
        // --- THIS IS THE CORRECTED FUNCTION TO RUN LOCATION SCRIPTS
        // ---
        function initializeLocationDropdowns() {
            // Guard: Only run this one time
            if (locationDropdownsInitialized) return;

            // This needs to run *after* the tab is visible
            setTimeout(() => {
                if (document.getElementById('locations-tab-content')) {
                    setupCharCounters();
                    manageActiveLocationLimit();
                    document.querySelectorAll('.active-location-checkbox').forEach(cb => {
                        cb.addEventListener('change', manageActiveLocationLimit);
                    });
                    locationDropdownsInitialized = true; // Mark as done
                }
            }, 0); // 0ms timeout waits for next browser tick
        }

        function updateTabDisplay() {
            Object.values(contentPanels).forEach(panel => { if(panel) panel.style.display = 'none'; });
            tabs.forEach(tab => tab.classList.toggle('active', tab.dataset.tab === activeTab));
            if (contentPanels[activeTab]) contentPanels[activeTab].style.display = 'block';

            // --- THIS IS THE KEY CHANGE ---
            // Call the init function when the tab is shown
            if (activeTab === 'locations') {
                initializeLocationDropdowns();
            }
            // --- END KEY CHANGE ---

            if (activeTab === 'rigs') fetchRigsData();
            if (activeTab === 'shared' && contentPanels['shared']) { fetchSharedItems(); }
            localStorage.setItem('activeConfigTab', activeTab);
        }
        tabs.forEach(tab => tab.addEventListener('click', () => { activeTab = tab.dataset.tab; updateTabDisplay(); }));
        updateTabDisplay(); // Run on page load

        // --- NEW: Trix Editor File Upload Handler ---
        function handleTrixAttachmentAdd(event) {
            if (!event.attachment.file) { return; }
            const file = event.attachment.file;
            const formData = new FormData();
            formData.append("file", file);
            event.attachment.setUploadProgress(0);
            fetch(window.NOVA_CONFIG_FORM.urls.uploadEditorImage, {
                method: "POST",
                body: formData
            })
            .then(response => response.json())
            .then(data => {
                if (data.url) {
                    event.attachment.setAttributes({
                        url: data.url,
                        href: data.url
                    });
                    event.attachment.setUploadProgress(100);
                } else if (data.error) {
                    console.error("Trix upload error:", data.error);
                    event.attachment.remove();
                    alert("Image upload failed: " + data.error);
                }
            })
            .catch(error => {
                console.error("Trix upload network error:", error);
                event.attachment.remove();
                alert("Image upload failed: Network error. See console for details.");
            });
        }
        document.addEventListener("trix-attachment-add", handleTrixAttachmentAdd);
        // --- END: Trix Editor File Upload Handler ---

        // --- Rigs Setup ---
        const seeingSelect = document.getElementById('seeing-select');
        if (seeingSelect) seeingSelect.addEventListener('change', updateSamplingInfo);
        const rigSortEl = document.getElementById('rig-sort');
        if (rigSortEl) {
            rigSortEl.value = rigSort;
            rigSortEl.addEventListener('change', () => {
                rigSort = rigSortEl.value;
                localStorage.setItem('rigSort', rigSort);
                fetch('/set_rig_sort_preference', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ sort: rigSort }) });
                if (rigsDataLoaded) { renderRigsUI(); updateSamplingInfo(); }
            });
        }

        // --- Fetch Details Logic ---
        const fetchDetailsForm = document.getElementById('fetch-details-form');
        if (fetchDetailsForm) fetchDetailsForm.addEventListener('submit', function(e) { e.preventDefault(); confirmAndFetchDetails(this); });


        // --- Dropdown Menu & Import Logic ---
        document.querySelectorAll('.dropdown-btn').forEach(button => {
            button.addEventListener('click', e => {
                document.querySelectorAll('.dropdown-content.show').forEach(d => { if (d !== button.nextElementSibling) d.classList.remove('show'); });
                button.nextElementSibling.classList.toggle('show');
            });
        });

        // --- Direct event binding for file input triggers (Safari compatibility) ---
        // Safari requires file input .click() to happen in direct response to user gesture.
        // Event delegation via document loses the "user activation" context in Safari.
        document.querySelectorAll('[data-action="trigger-file-input"]').forEach(trigger => {
            trigger.addEventListener('click', function(e) {
                e.preventDefault();
                e.stopPropagation();
                const inputId = this.dataset.targetId;
                const inputEl = document.getElementById(inputId);
                if (inputEl) {
                    console.log('[CONFIG_FORM] Direct trigger file input:', inputId);
                    inputEl.click();
                } else {
                    console.error('[CONFIG_FORM] File input not found:', inputId);
                }
            }, true); // Use capture phase for maximum Safari compatibility
        });

        // --- Event delegation for click actions (excludes trigger-file-input, handled above) ---
        document.addEventListener('click', function(e) {
            const target = e.target.closest('[data-action]');
            if (!target) return;

            const action = target.dataset.action;

            // trigger-file-input is handled by direct binding above; skip here
            if (action === 'trigger-file-input') {
                return;
            }

            console.log('[CONFIG_FORM] Click action triggered:', action, target);

            switch(action) {
                case 'edit-component':
                    console.log('[CONFIG_FORM] edit-component:', target.dataset.type, target.dataset.id);
                    populateComponentFormForEdit(
                        target.dataset.type,
                        target.dataset.id
                    );
                    break;
                case 'edit-rig':
                    console.log('[CONFIG_FORM] edit-rig:', target.dataset.id);
                    populateRigFormForEdit(target.dataset.id);
                    break;
                case 'import-shared':
                    console.log('[CONFIG_FORM] import-shared:', target.dataset.id, target.dataset.itemType);
                    importSharedItem(
                        target.dataset.id,
                        target.dataset.itemType,
                        target
                    );
                    break;
                case 'view-shared':
                    console.log('[CONFIG_FORM] view-shared:', target.dataset.url);
                    if (target.dataset.url) {
                        window.location.href = target.dataset.url;
                    }
                    break;
                case 'show-shared-notes':
                    console.log('[CONFIG_FORM] show-shared-notes:', target.dataset.objectName);
                    showSharedNotes(target.dataset.objectName, target.dataset.notes);
                    break;
                case 'close-notes-modal':
                    console.log('[CONFIG_FORM] close-notes-modal');
                    closeNotesModal();
                    break;
                default:
                    console.warn('[CONFIG_FORM] Unknown action:', action);
            }

            // Handle stop propagation AFTER processing data-action (if specified on element)
            if (target.dataset.stopPropagation === 'true') {
                e.stopPropagation();
            }
        });

        // --- Event delegation for form confirmations ---
        document.addEventListener('submit', function(e) {
            const confirmMsg = e.target.dataset.confirm;
            if (confirmMsg && !confirm(confirmMsg)) {
                e.preventDefault();
            }
        });

        // --- Event delegation for .hzn file input changes ---
        document.addEventListener('change', function(e) {
            if (e.target.classList.contains('hzn-file-input')) {
                console.log('[CONFIG_FORM] hzn-file-input change detected:', e.target);
                const textareaId = e.target.dataset.textareaId;
                if (textareaId) {
                    parseStellariumHorizon(e.target, textareaId);
                } else {
                    console.error('[CONFIG_FORM] hzn-file-input missing data-textarea-id:', e.target);
                }
            }
        });

        // Clean up event listeners when navigating away (prevents memory leaks)
        window.addEventListener('beforeunload', () => {
            document.removeEventListener("trix-attachment-add", handleTrixAttachmentAdd);
        });

        // Click outside to close dropdowns
        document.addEventListener('click', function(e) {
            const dropdownContent = e.target.closest('.dropdown-content');
            const dropdownBtn = e.target.closest('.dropdown-btn');
            if (!dropdownContent && !dropdownBtn) {
                document.querySelectorAll('.dropdown-content.show').forEach(d => d.classList.remove('show'));
            }
        });

    });

    function parseStellariumHorizon(fileInput, textareaId) {
        const file = fileInput.files[0];
        if (!file) {
            console.warn('[CONFIG_FORM] parseStellariumHorizon: No file selected');
            return;
        }

        // Validate textarea exists before processing
        const textarea = document.getElementById(textareaId);
        if (!textarea) {
            console.error('[CONFIG_FORM] parseStellariumHorizon: Target textarea not found:', textareaId);
            alert('Error: Could not find the horizon mask field. Please refresh the page and try again.');
            fileInput.value = '';
            return;
        }

        console.log('[CONFIG_FORM] Parsing .hzn file:', file.name, '-> textarea:', textareaId);

        const reader = new FileReader();

        reader.onerror = function(e) {
            console.error('[CONFIG_FORM] FileReader error:', e.target.error);
            alert('Error reading file. Please try again or select a different file.');
            fileInput.value = '';
        };

        reader.onload = function(e) {
            const lines = e.target.result.split(/\r?\n/);
            let points = [];

            for (const line of lines) {
                const trimmed = line.trim();
                if (!trimmed || trimmed.startsWith('#') || trimmed.startsWith(';')) continue;
                const parts = trimmed.split(/[\s,]+/);
                if (parts.length >= 2) {
                    const az = parseFloat(parts[0]);
                    const alt = parseFloat(parts[1]);
                    if (!isNaN(az) && !isNaN(alt)) {
                        points.push([Math.round(az * 10) / 10, Math.round(alt * 10) / 10]);
                    }
                }
            }

            if (points.length === 0) {
                console.warn('[CONFIG_FORM] No valid horizon data found in file');
                alert('No valid horizon data found in the file. Expected format: azimuth altitude (one pair per line).');
                fileInput.value = '';
                return;
            }

            // Sort by azimuth
            points.sort((a, b) => a[0] - b[0]);

            // Simplify if too many points: keep every Nth to get ~100
            if (points.length > 100) {
                const step = Math.ceil(points.length / 100);
                const simplified = [];
                for (let i = 0; i < points.length; i += step) {
                    simplified.push(points[i]);
                }
                // Always include the last point
                if (simplified[simplified.length - 1] !== points[points.length - 1]) {
                    simplified.push(points[points.length - 1]);
                }
                points = simplified;
            }

            const formatted = '[' + points.map(p => '[' + p[0] + ', ' + p[1] + ']').join(', ') + ']';
            textarea.value = formatted;
            console.log('[CONFIG_FORM] Successfully parsed', points.length, 'horizon points');
            fileInput.value = '';
        };
        reader.readAsText(file);
    }
