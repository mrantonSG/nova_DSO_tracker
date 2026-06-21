/* mobile_up_now.js — Up Now page logic (mobile view) */
/* Depends on i18n object and window.mobileDataChunkUrl / window.mobileStatusUrl */
/* rendered by the inline script in mobile_up_now.html. */

document.addEventListener('DOMContentLoaded', () => {
    const list = document.getElementById('object-list');
    const loadingContainer = document.getElementById('mobile-loading');
    const progressFill = document.getElementById('progress-fill');
    const loadingText = document.getElementById('loading-text');

    const filterActiveButton = document.getElementById('filter-active');
    const filterFramingButton = document.getElementById('filter-framing');
    const searchInput = document.getElementById('search-input');

    const noObjectsMessage = document.getElementById('loading-indicator');

    let allItems = [];

    // ── Filter state ──
    const FILTERS = {
        minAlt:   0,
        maxAlt:   0,
        minDur:   0,
        minMoon:  0,
        type:     '',
        const:    '',
    };

    // ── Slider live value display ──
    function initSlider(id, valId, suffix) {
        const slider = document.getElementById(id);
        const valEl  = document.getElementById(valId);
        if (!slider || !valEl) return;
        slider.addEventListener('input', () => {
            valEl.textContent = slider.value + suffix;
        });
    }
    initSlider('f-min-alt',  'val-min-alt',  '°');
    initSlider('f-max-alt',  'val-max-alt',  '°');
    initSlider('f-min-dur',  'val-min-dur',  'm');
    initSlider('f-min-moon', 'val-min-moon', '°');

    // ── Type chip selection (multi-select) ──
    document.getElementById('type-chips')
        ?.addEventListener('click', (e) => {
            const chip = e.target.closest('.m-type-chip');
            if (!chip) return;
            chip.classList.toggle('selected');
        });

    // ── Constellation chip population (from loaded data) ──
    function populateConstChips() {
        const container = document.getElementById('const-chips');
        if (!container) return;

        const raw = [...new Set(
            allItems
                .map(i => i.dataset.constellation)
                .filter(c => c && c.trim() !== '')
        )];

        const fullNames = raw.filter(c => c.length > 4);
        const abbrevs   = raw.filter(c => c.length <= 4);

        const fullPrefixes = new Set(fullNames.map(n => n.slice(0, 3)));

        const kept = [
            ...fullNames,
            ...abbrevs.filter(a => !fullPrefixes.has(a.slice(0, 3)))
        ];

        kept.sort((a, b) => a.localeCompare(b));

        container.innerHTML = '';
        kept.forEach(c => {
            const chip = document.createElement('div');
            chip.className = 'm-type-chip';
            chip.dataset.const = c;
            chip.textContent = c.charAt(0).toUpperCase() + c.slice(1);
            chip.addEventListener('click', () => chip.classList.toggle('selected'));
            container.appendChild(chip);
        });
    }

    let currentSort = 'alt';

    function renderList() {
        let searchedItems = [];
        if (currentSearch === '') {
            searchedItems = [...allItems];
        } else {
            searchedItems = allItems.filter(item => {
                return item.dataset.searchText.includes(currentSearch);
            });
        }

        let itemsToShow = [...searchedItems];

        if (filterActive) {
            itemsToShow = itemsToShow.filter(item => item.dataset.isActive === 'true');
        }

        if (filterFraming) {
            itemsToShow = itemsToShow.filter(item => item.dataset.hasFraming === 'true');
        }

        if (currentSort === 'alt') {
            itemsToShow.sort((a, b) => {
                return parseFloat(b.dataset.sortAlt) - parseFloat(a.dataset.sortAlt);
            });
        } else {
            itemsToShow.sort((a, b) => {
                return parseFloat(b.dataset.sortDur) - parseFloat(a.dataset.sortDur);
            });
        }

        allItems.forEach(item => item.style.display = 'none');

        itemsToShow.forEach(item => {
            item.style.display = 'flex';
            list.appendChild(item);
        });

        const countEl = document.getElementById('result-count');
        if (countEl) {
            const total = allItems.length;
            const visible = allItems.filter(item => item.style.display !== 'none').length;
            countEl.textContent = visible < total
                ? `${visible} / ${total}`
                : `${total}`;
        }

        if (noObjectsMessage) {
            if (allItems.length === 0) {
                noObjectsMessage.textContent = i18n.noObjectsTryAdding;
                noObjectsMessage.style.display = 'block';
            } else if (itemsToShow.length === 0) {
                if (searchedItems.length === 0) {
                    noObjectsMessage.textContent = i18n.noSearchMatch;
                } else {
                    noObjectsMessage.textContent = i18n.noFilterMatch;
                }
                noObjectsMessage.style.display = 'block';
            } else {
                noObjectsMessage.style.display = 'none';
            }
        }

        filterActiveButton.classList.toggle('active', filterActive);
        filterFramingButton.classList.toggle('active', filterFraming);
    }

    let currentSearch = '';
    let filterActive = false;
    let filterFraming = false;

    searchInput.addEventListener('input', () => {
        currentSearch = searchInput.value.trim().toLowerCase();
        renderList();
    });

    filterActiveButton.addEventListener('click', () => {
        filterActive = !filterActive;
        renderList();
    });

    filterFramingButton.addEventListener('click', () => {
        filterFraming = !filterFraming;
        renderList();
    });

    // ── Sort dropdown ──
    const sortBtn      = document.getElementById('sort-btn');
    const sortDropdown = document.getElementById('sort-dropdown');
    const sortLabelEl  = document.getElementById('sort-label');

    sortBtn?.addEventListener('click', (e) => {
        e.stopPropagation();
        const isOpen = sortDropdown.classList.contains('open');
        if (!isOpen) {
            const rect = sortBtn.getBoundingClientRect();
            sortDropdown.style.top  = (rect.bottom + 6) + 'px';
            sortDropdown.style.right = (window.innerWidth - rect.right) + 'px';
        }
        sortDropdown.classList.toggle('open');
    });

    document.addEventListener('click', () => {
        sortDropdown?.classList.remove('open');
    });

    sortDropdown?.addEventListener('click', (e) => {
        const option = e.target.closest('.m-sort-option');
        if (!option) return;
        currentSort = option.dataset.sort;
        sortDropdown.querySelectorAll('.m-sort-option')
            .forEach(o => o.classList.toggle('active', o.dataset.sort === currentSort));
        sortLabelEl.textContent = option.textContent.trim();
        sortDropdown.classList.remove('open');
        refreshListItemsAndSort();
    });

    // ── Filter sheet ──
    const filterSheet   = document.getElementById('filter-sheet');
    const filterOverlay = document.getElementById('filter-overlay');
    const filterBtn     = document.getElementById('open-filters');
    const filterCountEl = document.getElementById('filter-count');

    function openSheet() {
        filterSheet.classList.add('open');
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                filterSheet.classList.add('visible');
            });
        });
        filterOverlay.classList.add('open');
        document.body.classList.add('sheet-open');
    }

    function closeSheet() {
        filterSheet.classList.remove('visible');
        filterSheet.addEventListener('transitionend', function handler() {
            filterSheet.classList.remove('open');
            filterSheet.removeEventListener('transitionend', handler);
        });
        filterOverlay.classList.remove('open');
        document.body.classList.remove('sheet-open');
    }

    function countActiveFilters() {
        let n = 0;
        if (FILTERS.minAlt > 0)    n++;
        if (FILTERS.maxAlt > 0)    n++;
        if (FILTERS.minDur > 0)    n++;
        if (FILTERS.minMoon > 0)   n++;
        if (FILTERS.type)          n++;
        if (FILTERS.const)         n++;
        return n;
    }

    function updateFilterBadge() {
        const n = countActiveFilters();
        filterCountEl.textContent = n;
        filterCountEl.style.display = n > 0 ? 'flex' : 'none';
        filterBtn.classList.toggle('has-filters', n > 0);
    }

    filterBtn?.addEventListener('click', openSheet);
    filterOverlay?.addEventListener('click', closeSheet);

    document.getElementById('filter-apply')?.addEventListener('click', () => {
        FILTERS.minAlt  = parseInt(document.getElementById('f-min-alt').value)  || 0;
        FILTERS.maxAlt  = parseInt(document.getElementById('f-max-alt').value)  || 0;
        FILTERS.minDur  = parseInt(document.getElementById('f-min-dur').value)  || 0;
        FILTERS.minMoon = parseInt(document.getElementById('f-min-moon').value) || 0;
        const typeChips = document.querySelectorAll('#type-chips .m-type-chip.selected');
        FILTERS.type    = Array.from(typeChips).map(c => c.dataset.type).join(',');
        FILTERS.const = document.getElementById('f-const').value.trim();
        updateFilterBadge();
        closeSheet();
        refreshListItemsAndSort();
    });

    document.getElementById('filter-reset')?.addEventListener('click', () => {
        FILTERS.minAlt = 0; FILTERS.maxAlt = 0;
        FILTERS.minDur = 0; FILTERS.minMoon = 0;
        FILTERS.type = ''; FILTERS.const = '';
        document.getElementById('f-min-alt').value  = 0;
        document.getElementById('f-max-alt').value  = 0;
        document.getElementById('f-min-dur').value  = 0;
        document.getElementById('f-min-moon').value = 0;
        document.getElementById('val-min-alt').textContent = '0°';
        document.getElementById('val-max-alt').textContent = '0°';
        document.getElementById('val-min-dur').textContent = '0m';
        document.getElementById('val-min-moon').textContent = '0°';
        document.querySelectorAll('.m-type-chip.selected').forEach(c => c.classList.remove('selected'));
        document.getElementById('f-const').value = '';
        updateFilterBadge();
        refreshListItemsAndSort();
    });

    // --- Chunked Data Fetching & Caching ---
    const CHUNK_SIZE = 25;

    const activeLoc = sessionStorage.getItem('nova_mobile_location') || 'default';
    const CACHE_KEY = `nova_mobile_cache_${activeLoc}`;
    const CACHE_EXPIRY = 5 * 60 * 1000;

    function getTrendClass(trend) {
        if (trend && trend.includes('↑')) return 'trend-up';
        if (trend && trend.includes('↓')) return 'trend-down';
        return 'trend-flat';
    }

    function getBadgeClass(alt) {
        const altVal = parseFloat(alt);
        if (altVal >= 45) return 'badge-high';
        if (altVal >= 20) return 'badge-med';
        return 'badge-low';
    }

    function formatDuration(mins) {
        const val = parseInt(mins);
        if (val === 0) return '0m';
        return val + 'm';
    }

    async function fetchMobileData() {
        const cachedRaw = sessionStorage.getItem(CACHE_KEY);
        if (cachedRaw) {
            try {
                const cached = JSON.parse(cachedRaw);
                if (Date.now() - cached.timestamp < CACHE_EXPIRY) {
                    loadingContainer.style.display = 'none';
                    list.style.display = 'block';

                    cached.data.forEach(obj => createListItem(obj));
                    populateConstChips();
                    refreshListItemsAndSort();
                    return;
                }
            } catch (e) { console.warn("Cache parse failed, fetching fresh."); }
        }

        let offset = 0;
        let total = 1;
        let gatheredData = [];

        loadingContainer.style.display = 'block';
        list.style.display = 'none';

        try {
            while (offset < total) {
                const response = await fetch(window.mobileDataChunkUrl + '?offset=' + offset + '&limit=' + CHUNK_SIZE);
                const json = await response.json();

                if (!json.data) break;

                total = json.total;

                json.data.forEach(obj => {
                    createListItem(obj);
                    gatheredData.push(obj);
                });

                offset += CHUNK_SIZE;
                const percentage = Math.min(100, Math.round((offset / total) * 100));
                progressFill.style.width = percentage + '%';
                loadingText.textContent = i18n.calculatedOf.replace('%(count)d', Math.min(offset, total)).replace('%(total)d', total);
            }

            try {
                sessionStorage.setItem(CACHE_KEY, JSON.stringify({
                    timestamp: Date.now(),
                    data: gatheredData
                }));
            } catch (e) { console.warn("Cache save failed (quota?)", e); }

            setTimeout(() => {
                loadingContainer.style.display = 'none';
                list.style.display = 'block';
                populateConstChips();
                refreshListItemsAndSort();
            }, 500);

        } catch (e) {
            loadingText.textContent = i18n.errorLoading;
            console.error(e);
        }
    }

    function createListItem(obj) {
        const li = document.createElement('li');
        li.className = 'm-card';

        const alt = parseFloat(obj['Altitude Current']) || 0;
        const dur = parseInt(obj['Observable Duration (min)']) || 0;
        const isActive = obj.ActiveProject ? 'true' : 'false';
        const hasFraming = obj.has_framing ? 'true' : 'false';

        li.dataset.sortAlt = alt;
        li.dataset.sortDur = dur;
        li.dataset.maxAlt = parseFloat(obj['Max Altitude (°)']) || 0;
        li.dataset.isActive = isActive;
        li.dataset.hasFraming = hasFraming;
        li.dataset.searchText = (obj['Common Name'] + ' ' + obj['Object']).toLowerCase();
        li.dataset.moonSep       = parseFloat(obj['Angular Separation (°)']) || 999;
        li.dataset.type          = (obj['Type'] || '').toLowerCase();
        li.dataset.constellation = (obj['Constellation'] || '').toLowerCase();
        li.style.display = 'none';
        allItems.push(li);

        const safeObj = encodeURIComponent(obj.Object);

        const badgeClass = getBadgeClass(alt);
        const trendClass = getTrendClass(obj['Trend']);

        const trendSymbol = obj['Trend'] && obj['Trend'].includes('↑') ? '↑' :
                         obj['Trend'] && obj['Trend'].includes('↓') ? '↓' : '–';

        const activeDotHtml = isActive === 'true' ? '<span class="m-card-active-dot"></span>' : '';

        li.innerHTML = `
            <div class="m-card-badge ${badgeClass}">${Math.round(alt)}°</div>
            <div class="m-card-body">
                <div class="m-card-title">
                    <span class="m-card-name">${obj['Common Name']}</span>
                    <span class="m-card-id">${obj['Object']}</span>
                    ${activeDotHtml}
                </div>
                <div class="m-card-meta">
                    <span class="m-trend ${trendClass}">${trendSymbol}</span>
                    <span class="m-meta-item">AZ <b>${obj['Azimuth Current']}°</b></span>
                    <span class="m-meta-item">MAX <b>${obj['Max Altitude (°)']}°</b></span>
                    <span class="m-meta-item">DUR <b>${formatDuration(dur)}</b></span>
                </div>
            </div>
            <span class="m-card-chevron">›</span>
        `;

        li.addEventListener('click', () => {
            window.location.href = '/m/object/' + safeObj;
        });

        list.appendChild(li);
    }

    function refreshListItemsAndSort() {
        const searchTerm    = (document.getElementById('search-input')?.value || '').toLowerCase().trim();
        const filterActive  = document.getElementById('filter-active')?.classList.contains('active') || false;
        const filterFraming = document.getElementById('filter-framing')?.classList.contains('active') || false;

        let visibleCount = 0;

        allItems.forEach(item => {
            const alt     = parseFloat(item.dataset.sortAlt) || 0;
            const maxAlt  = parseFloat(item.dataset.maxAlt) || 0;
            const dur     = parseInt(item.dataset.sortDur) || 0;
            const moon    = parseFloat(item.dataset.moonSep ?? 999);
            const type    = (item.dataset.type || '').toLowerCase();
            const cons    = (item.dataset.constellation || '').toLowerCase();

            const matchSearch   = !searchTerm  || item.dataset.searchText.includes(searchTerm);
            const matchActive   = !filterActive  || item.dataset.isActive === 'true';
            const matchFraming  = !filterFraming || item.dataset.hasFraming === 'true';
            const matchMinAlt   = alt    >= FILTERS.minAlt;
            const matchMaxAlt   = FILTERS.maxAlt === 0 || maxAlt >= FILTERS.maxAlt;
            const matchDur      = dur  >= FILTERS.minDur;
            const matchMoon     = moon >= FILTERS.minMoon;
            const matchType     = !FILTERS.type  ||
                FILTERS.type.split(',').map(t => t.trim().toLowerCase())
                    .some(t => type.includes(t));
            const matchConst    = !FILTERS.const ||
                FILTERS.const.split(',').map(c => c.trim()).some(filterVal => {
                    const f3 = filterVal.slice(0, 3);
                    const c3 = cons.slice(0, 3);
                    return f3 === c3;
                });

            const visible = matchSearch && matchActive && matchFraming &&
                            matchMinAlt && matchMaxAlt && matchDur &&
                            matchMoon && matchType && matchConst;

            item.style.display = visible ? 'flex' : 'none';
            if (visible) visibleCount++;
        });

        const sorted = allItems.filter(i => i.style.display !== 'none');
        const sortByAlt = currentSort === 'alt';
        sorted.sort((a, b) => sortByAlt
            ? parseFloat(b.dataset.sortAlt) - parseFloat(a.dataset.sortAlt)
            : parseInt(b.dataset.sortDur)   - parseInt(a.dataset.sortDur));
        const list = document.getElementById('object-list');
        sorted.forEach(item => list.appendChild(item));

        const countEl = document.getElementById('result-count');
        if (countEl) {
            const total = allItems.length;
            countEl.textContent = visibleCount < total
                ? `${visibleCount} / ${total}`
                : `${total}`;
        }

        const objectsEl = document.getElementById('status-objects');
        if (objectsEl) objectsEl.textContent = allItems.length + ' objects';

        const emptyEl = document.getElementById('loading-indicator');
        if (emptyEl) emptyEl.style.display = visibleCount === 0 ? 'block' : 'none';
    }

    async function fetchMobileStatus() {
        try {
            const response = await fetch(window.mobileStatusUrl);
            const data = await response.json();

            if (data.moon_phase !== undefined) {
                const moonEl = document.getElementById('status-moon');
                if (moonEl) {
                    moonEl.textContent = data.moon_phase + '%';
                }
            }

            if (data.dusk_dawn) {
                const duskDawnEl = document.getElementById('status-dusk-dawn');
                if (duskDawnEl) {
                    duskDawnEl.textContent = data.dusk_dawn;
                }
            }
        } catch (e) {
            console.warn('Failed to fetch mobile status:', e);
        }
    }

    fetchMobileData();
    fetchMobileStatus();
});
