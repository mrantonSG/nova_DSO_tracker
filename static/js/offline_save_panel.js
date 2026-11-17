/**
 * offline_save_panel.js - the Framing Assistant's "Save for offline" panel and
 * its "Saved for offline" list (offline sky, piece B2).
 *
 * The markup lives in templates/graph_view.html (ids offline-save-*); this file
 * fills it and talks to the /api/offline/* routes. graph_view_chart.js creates
 * one panel per page with NovaOfflineSavePanel.create(deps) and:
 *   - calls panel.toggle() from the "Save for offline" button,
 *   - calls panel.close() when the modal opens and when it closes,
 *   - routes keydown (capture phase) through panel.handleKeydown(e).
 *
 * Every timer (estimate debounce, progress polling) belongs to an open panel:
 * close() clears them all, and responses that arrive after close are dropped.
 *
 * deps: { doc, fetch, t, confirm, timers, surveys, getFrame, getBaseId,
 *         getBlendId, getName, isConfirmOpen }
 *   getFrame() -> { ra, dec, frameW, frameH, rigId }
 *   timers     -> { setTimeout, clearTimeout, setInterval, clearInterval }
 */
(function (root) {
    'use strict';

    // Detail choice -> detail offset sent to the API. The server turns the rig's
    // image scale into a HiPS level and adds the offset; levels are never shown.
    var DETAIL_CHOICES = [
        { value: '-1', offset: -1, key: 'offline_detail_lighter' },
        { value: '0',  offset: 0,  key: 'offline_detail_rig' },
        { value: '1',  offset: 1,  key: 'offline_detail_sharper' },
    ];
    var DEFAULT_OFFSET = 0;
    var ESTIMATE_DEBOUNCE_MS = 350;
    var POLL_MS = 1500;
    var ACTIVE = ['queued', 'running', 'paused'];
    var MB = 1024 * 1024;

    function offsetForValue(value) {
        for (var i = 0; i < DETAIL_CHOICES.length; i++) {
            if (DETAIL_CHOICES[i].value === String(value)) return DETAIL_CHOICES[i].offset;
        }
        return DEFAULT_OFFSET;
    }

    function choiceKeyForOffset(offset) {
        for (var i = 0; i < DETAIL_CHOICES.length; i++) {
            if (DETAIL_CHOICES[i].offset === offset) return DETAIL_CHOICES[i].key;
        }
        return null;
    }

    function isActive(status) { return ACTIVE.indexOf(status) >= 0; }

    function formatMb(bytes) {
        var mb = (Number(bytes) || 0) / MB;
        return mb >= 100 ? String(Math.round(mb)) : mb.toFixed(1);
    }

    function formatScale(arcsec) {
        var v = Number(arcsec);
        if (!isFinite(v) || v <= 0) return '';
        return v >= 10 ? v.toFixed(0) : v.toFixed(1);
    }

    /** Human status for a save job/summary {status, done, total, missing} -> {text, tone}. */
    function statusText(t, job) {
        var status = job.status || 'queued';
        if (status === 'queued') return { text: t('offline_status_waiting'), tone: '' };
        if (status === 'running') {
            if (job.total > 0) {
                return { text: t('offline_status_downloading', { done: job.done || 0, total: job.total }), tone: '' };
            }
            return { text: t('offline_status_waiting'), tone: '' };
        }
        if (status === 'paused') return { text: t('offline_status_paused'), tone: 'warning' };
        if (status === 'done') {
            if (job.missing > 0) return { text: t('offline_status_done_missing', { count: job.missing }), tone: '' };
            return { text: t('offline_status_done'), tone: 'success' };
        }
        // A save only stops early when the space runs out (reason "no_space").
        if (status === 'stopped') return { text: t('offline_status_no_space'), tone: 'warning' };
        if (status === 'cancelled') return { text: t('offline_status_cancelled'), tone: '' };
        return { text: t('offline_status_failed'), tone: 'error' };
    }

    /** Plain-words reason for an estimate/save refusal code. */
    function reasonText(t, code) {
        switch (code) {
            case 'over_cap': return t('offline_reason_over_cap');
            case 'disk_headroom': return t('offline_reason_disk');
            case 'no_surveys': return t('offline_reason_no_surveys');
            case 'rig_required':
            case 'rig_not_found': return t('offline_reason_no_rig');
            case 'rig_no_image_scale': return t('offline_reason_rig_scale');
            case 'radius_too_large': return t('offline_reason_too_large');
            case 'nothing': return t('offline_reason_nothing');
            default: return t('offline_reason_error');
        }
    }

    function frameValid(f) {
        return !!f && isFinite(f.ra) && isFinite(f.dec) && f.frameW > 0 && f.frameH > 0 && !!f.rigId;
    }

    function frameQuery(f) {
        return 'ra=' + f.ra + '&dec=' + f.dec + '&frame_w_deg=' + f.frameW +
            '&frame_h_deg=' + f.frameH + '&rig_id=' + encodeURIComponent(f.rigId);
    }

    function readJson(resp) {
        return resp.json().then(function (d) { return { ok: resp.ok, status: resp.status, data: d }; },
                                function () { return { ok: resp.ok, status: resp.status, data: null }; });
    }

    function create(deps) {
        var doc = deps.doc;
        var t = deps.t;
        var timers = deps.timers;
        var surveys = deps.surveys || {};
        var surveyIds = Object.keys(surveys);

        function $(id) { return doc.getElementById(id); }
        function el(tag, cls, text) {
            var e = doc.createElement(tag);
            if (cls) e.className = cls;
            if (text !== undefined && text !== null) e.textContent = String(text);
            return e;
        }

        var state = {
            open: false,
            estTimer: null,
            pollTimer: null,
            seq: 0,                 // bumps on every close: late responses are dropped
            estSeq: 0,
            coverage: null,         // {key, bySurvey: {id: survey estimate}} for every survey
            checkedEstimate: null,  // estimate for the checked surveys (total + verdict)
            blocked: true,
            saves: [],
            rows: {},               // save id -> {status, cancel}
            surveyRows: {},         // survey id -> {input, note, size, row}
            busy: false,
        };

        // --- open / close -------------------------------------------------
        function setExpanded(open) {
            var btn = $('save-offline-btn');
            if (btn) btn.setAttribute('aria-expanded', open ? 'true' : 'false');
        }

        function stopTimers() {
            if (state.estTimer !== null) { timers.clearTimeout(state.estTimer); state.estTimer = null; }
            if (state.pollTimer !== null) { timers.clearInterval(state.pollTimer); state.pollTimer = null; }
        }

        function open() {
            var panel = $('offline-save-panel');
            if (!panel) return;
            state.open = true;
            state.coverage = null;
            state.checkedEstimate = null;
            panel.hidden = false;
            setExpanded(true);
            var sel = $('offline-detail-select');
            if (sel) sel.value = String(DEFAULT_OFFSET);
            renderSurveys();
            scheduleEstimate(0);
            loadSaves();
        }

        function close() {
            state.open = false;
            state.seq++;
            stopTimers();
            var panel = $('offline-save-panel');
            if (panel) panel.hidden = true;
            setExpanded(false);
        }

        function toggle() { if (state.open) close(); else open(); }

        function handleKeydown(e) {
            if (!state.open || !e || (e.key !== 'Escape' && e.key !== 'Esc')) return false;
            // A confirm dialog on top owns Escape.
            if (deps.isConfirmOpen && deps.isConfirmOpen()) return false;
            close();
            if (e.stopPropagation) e.stopPropagation();   // the modal stays open
            if (e.preventDefault) e.preventDefault();
            return true;
        }

        // --- surveys ------------------------------------------------------
        function checkedSurveys() {
            return surveyIds.filter(function (sid) {
                var r = state.surveyRows[sid];
                return r && r.input.checked && !r.input.disabled;
            });
        }

        function renderSurveys() {
            var list = $('offline-survey-list');
            if (!list) return;
            list.textContent = '';
            state.surveyRows = {};
            var base = deps.getBaseId ? deps.getBaseId() : '';
            var blend = deps.getBlendId ? deps.getBlendId() : '';
            surveyIds.forEach(function (sid) {
                var row = el('label', 'offline-survey-row');
                var input = el('input', 'offline-survey-check');
                input.type = 'checkbox';
                input.value = sid;
                input.checked = (sid === base || sid === blend);
                input.addEventListener('change', function () { scheduleEstimate(ESTIMATE_DEBOUNCE_MS); });
                var name = el('span', 'offline-survey-name', (surveys[sid] && surveys[sid].label) || sid);
                var size = el('span', 'offline-survey-size', '');
                var note = el('span', 'offline-survey-note', '');
                note.hidden = true;
                row.appendChild(input);
                row.appendChild(name);
                row.appendChild(size);
                row.appendChild(note);
                list.appendChild(row);
                state.surveyRows[sid] = { input: input, note: note, size: size, row: row };
            });
        }

        function applyCoverage() {
            var by = state.coverage ? state.coverage.bySurvey : {};
            surveyIds.forEach(function (sid) {
                var r = state.surveyRows[sid];
                if (!r) return;
                var s = by[sid];
                var notCovered = !!s && s.orders && s.orders.length > 0 && s.tiles_total === 0;
                r.input.disabled = notCovered;
                if (notCovered) r.input.checked = false;
                r.row.className = 'offline-survey-row' + (notCovered ? ' is-disabled' : '');
                if (notCovered) {
                    r.note.textContent = t('offline_not_covered');
                    r.note.hidden = false;
                } else if (s && s.capped) {
                    r.note.textContent = t('offline_max_detail');
                    r.note.hidden = false;
                } else {
                    r.note.textContent = '';
                    r.note.hidden = true;
                }
            });
        }

        // --- estimate -----------------------------------------------------
        function selectedOffset() {
            var sel = $('offline-detail-select');
            return offsetForValue(sel ? sel.value : DEFAULT_OFFSET);
        }

        function scheduleEstimate(delay) {
            if (!state.open) return;
            if (state.estTimer !== null) timers.clearTimeout(state.estTimer);
            setSaveBlocked(true);
            setEstimating();
            state.estTimer = timers.setTimeout(function () {
                state.estTimer = null;
                runEstimate();
            }, delay);
        }

        function setEstimating() {
            var total = $('offline-estimate-total');
            if (total) total.textContent = t('offline_estimating');
        }

        function setSaveBlocked(blocked, reason) {
            state.blocked = blocked;
            var btn = $('offline-save-start');
            if (btn) btn.disabled = blocked || state.busy;
            var r = $('offline-save-reason');
            if (r) {
                r.textContent = reason || '';
                r.hidden = !reason;
            }
        }

        function estimateUrl(frame, offset, ids) {
            return '/api/offline/estimate?' + frameQuery(frame) + '&detail_offset=' + offset +
                '&surveys=' + ids.map(encodeURIComponent).join(',');
        }

        function runEstimate() {
            var seq = state.seq;
            var mine = ++state.estSeq;
            var frame = deps.getFrame();
            var offset = selectedOffset();
            var stale = function () { return seq !== state.seq || mine !== state.estSeq || !state.open; };
            if (!frameValid(frame)) {
                renderEstimate(null);
                setSaveBlocked(true, reasonText(t, frame && !frame.rigId ? 'rig_required' : 'error'));
                return Promise.resolve();
            }
            var key = frameQuery(frame) + '|' + offset;
            var coverageP = (state.coverage && state.coverage.key === key)
                ? Promise.resolve(null)
                : deps.fetch(estimateUrl(frame, offset, surveyIds)).then(readJson);
            return coverageP.then(function (cov) {
                if (stale()) return null;
                if (cov) {
                    if (!cov.ok || !cov.data || !cov.data.surveys) {
                        renderEstimate(null);
                        setScale(null);
                        setSaveBlocked(true, reasonText(t, cov.data && cov.data.code));
                        return null;
                    }
                    var by = {};
                    cov.data.surveys.forEach(function (s) { if (s.survey_id) by[s.survey_id] = s; });
                    state.coverage = { key: key, bySurvey: by, data: cov.data };
                    applyCoverage();
                }
                setScale(state.coverage.data);
                var ids = checkedSurveys();
                if (!ids.length) {
                    state.checkedEstimate = null;
                    renderEstimate(null);
                    setSaveBlocked(true, reasonText(t, 'no_surveys'));
                    return null;
                }
                return deps.fetch(estimateUrl(frame, offset, ids)).then(readJson).then(function (res) {
                    if (stale()) return;
                    if (!res.ok || !res.data || !res.data.total) {
                        state.checkedEstimate = null;
                        renderEstimate(null);
                        setSaveBlocked(true, reasonText(t, res.data && res.data.code));
                        return;
                    }
                    state.checkedEstimate = res.data;
                    renderEstimate(res.data);
                    if (res.data.reject) {
                        setSaveBlocked(true, reasonText(t, res.data.reject.code));
                    } else if (!res.data.total.tiles_total) {
                        setSaveBlocked(true, reasonText(t, 'nothing'));
                    } else {
                        setSaveBlocked(false);
                    }
                });
            }).catch(function () {
                if (stale()) return;
                renderEstimate(null);
                setSaveBlocked(true, reasonText(t, 'error'));
            });
        }

        function setScale(data) {
            var out = $('offline-detail-scale');
            if (!out) return;
            if (!data || !data.requested_pixel_scale_arcsec || !data.image_scale_arcsec) {
                out.textContent = '';
                return;
            }
            out.textContent = t('offline_detail_scale', {
                scale: formatScale(data.requested_pixel_scale_arcsec),
                rig: formatScale(data.image_scale_arcsec),
            });
        }

        function sizeText(tiles, bytes) {
            return t('offline_tiles_mb', { tiles: tiles, mb: formatMb(bytes) });
        }

        function renderEstimate(data) {
            var by = {};
            if (data && data.surveys) data.surveys.forEach(function (s) { if (s.survey_id) by[s.survey_id] = s; });
            surveyIds.forEach(function (sid) {
                var r = state.surveyRows[sid];
                if (!r) return;
                var s = by[sid];
                r.size.textContent = s ? sizeText(s.tiles_total, (s.est_bytes || 0) + (s.adopt_bytes || 0)) : '';
            });
            var total = $('offline-estimate-total');
            var free = $('offline-estimate-free');
            if (total) {
                total.textContent = data ? sizeText(data.total.tiles_total,
                    (data.total.est_bytes || 0) + (data.total.adopt_bytes || 0)) : '–';
            }
            if (free) free.textContent = data ? formatMb(data.free_bytes) + ' MB' : '–';
        }

        // --- save ---------------------------------------------------------
        function startSave() {
            if (!state.open || state.blocked || state.busy) return Promise.resolve();
            var frame = deps.getFrame();
            var ids = checkedSurveys();
            if (!frameValid(frame) || !ids.length) return Promise.resolve();
            var seq = state.seq;
            state.busy = true;
            setSaveBlocked(true);
            return deps.fetch('/api/offline/saves', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name: (deps.getName && deps.getName()) || 'RA/Dec',
                    ra: frame.ra, dec: frame.dec, frame_w_deg: frame.frameW, frame_h_deg: frame.frameH,
                    rig_id: frame.rigId, surveys: ids, detail_offset: selectedOffset(),
                }),
            }).then(readJson).then(function (res) {
                state.busy = false;
                if (seq !== state.seq) return;
                if (res.ok && res.data && res.data.job_id) {
                    state.coverage = null;      // reserved space changed: re-estimate
                    scheduleEstimate(0);
                    loadSaves();
                    return;
                }
                setSaveBlocked(true, reasonText(t, res.data && res.data.code));
            }).catch(function () {
                state.busy = false;
                if (seq !== state.seq) return;
                setSaveBlocked(true, reasonText(t, 'error'));
            });
        }

        // --- saved list + progress -----------------------------------------
        function loadSaves() {
            var seq = state.seq;
            return deps.fetch('/api/offline/saves').then(readJson).then(function (res) {
                if (seq !== state.seq || !state.open) return;
                state.saves = (res.ok && res.data && res.data.saves) || [];
                renderSaves();
                syncPolling();
            }).catch(function () {});
        }

        function surveyLabel(save, i) {
            var id = save.survey_ids && save.survey_ids[i];
            return (id && surveys[id] && surveys[id].label) || id || save.surveys[i];
        }

        function formatDate(iso) {
            if (!iso) return '';
            var d = new Date(iso);
            if (isNaN(d.getTime())) return '';
            try {
                return d.toLocaleDateString(deps.locale || undefined);
            } catch (e) {
                return iso.slice(0, 10);
            }
        }

        function renderSaves() {
            var list = $('offline-saves-list');
            if (!list) return;
            list.textContent = '';
            state.rows = {};
            if (!state.saves.length) {
                list.appendChild(el('div', 'offline-saves-empty', t('offline_none_saved')));
                return;
            }
            state.saves.forEach(function (s) {
                var row = el('div', 'offline-save-row');
                var main = el('div', 'offline-save-main');
                var title = el('div', 'offline-save-name');
                title.appendChild(el('span', '', s.name || 'RA/Dec'));
                if (s.partial && (s.status === 'cancelled' || s.status === 'stopped')) {
                    title.appendChild(el('span', 'offline-save-badge', t('offline_partial')));
                }
                main.appendChild(title);

                var parts = [];
                var names = (s.surveys || []).map(function (_x, i) { return surveyLabel(s, i); });
                if (names.length) parts.push(names.join(', '));
                var choiceKey = s.legacy ? 'offline_earlier_version' : choiceKeyForOffset(s.detail_offset);
                if (choiceKey) parts.push(t(choiceKey));
                parts.push(formatMb(s.bytes) + ' MB');
                var date = formatDate(s.created);
                if (date) parts.push(date);
                main.appendChild(el('div', 'offline-save-meta', parts.join(' · ')));

                var side = el('div', 'offline-save-side');
                var status = el('span', 'offline-save-status', '');
                side.appendChild(status);
                var cancel = el('button', 'inline-button-ghost offline-save-cancel', t('offline_cancel'));
                cancel.type = 'button';
                cancel.addEventListener('click', function () { cancelSave(s.id); });
                side.appendChild(cancel);
                var del = el('button', 'inline-button-danger offline-save-delete', t('delete'));
                del.type = 'button';
                del.addEventListener('click', function () { deleteSave(s); });
                side.appendChild(del);

                row.appendChild(main);
                row.appendChild(side);
                list.appendChild(row);
                state.rows[s.id] = { status: status, cancel: cancel };
                applyStatus(s.id, { status: s.status, missing: s.missing });
            });
        }

        function applyStatus(id, job) {
            var r = state.rows[id];
            if (!r) return;
            var st = statusText(t, job);
            r.status.textContent = st.text;
            if (st.tone) r.status.setAttribute('data-tone', st.tone);
            else r.status.removeAttribute('data-tone');
            r.cancel.hidden = !isActive(job.status);
        }

        function activeIds() {
            return state.saves.filter(function (s) { return isActive(s.status); }).map(function (s) { return s.id; });
        }

        function syncPolling() {
            var want = state.open && activeIds().length > 0;
            if (want && state.pollTimer === null) {
                state.pollTimer = timers.setInterval(pollTick, POLL_MS);
            } else if (!want && state.pollTimer !== null) {
                timers.clearInterval(state.pollTimer);
                state.pollTimer = null;
            }
        }

        function pollTick() {
            if (!state.open) { syncPolling(); return Promise.resolve(); }
            var seq = state.seq;
            var ids = activeIds();
            return Promise.all(ids.map(function (id) {
                return deps.fetch('/api/offline/saves/' + encodeURIComponent(id) + '/progress')
                    .then(readJson).then(function (res) {
                        if (seq !== state.seq) return false;
                        if (res.status === 404) return true;                  // deleted elsewhere
                        var job = res.ok && res.data && res.data.job;
                        if (!job) return false;
                        applyStatus(id, job);
                        var save = state.saves.filter(function (s) { return s.id === id; })[0];
                        return !!save && !isActive(job.status);
                    }).catch(function () { return false; });
            })).then(function (changed) {
                if (seq !== state.seq || !state.open) return;
                if (changed.some(Boolean)) {
                    state.coverage = null;
                    scheduleEstimate(0);
                    return loadSaves();
                }
            });
        }

        function cancelSave(id) {
            var seq = state.seq;
            return deps.fetch('/api/offline/saves/' + encodeURIComponent(id) + '/cancel', { method: 'POST' })
                .then(function () { if (seq === state.seq && state.open) return pollTick(); })
                .catch(function () {});
        }

        function deleteSave(save) {
            var seq = state.seq;
            return Promise.resolve(deps.confirm(t('offline_delete_confirm', { name: save.name || 'RA/Dec' }), {
                confirmLabel: t('delete'), cancelLabel: t('offline_cancel'), danger: true,
            })).then(function (ok) {
                if (!ok || seq !== state.seq) return;
                return deps.fetch('/api/offline/saves/' + encodeURIComponent(save.id), { method: 'DELETE' })
                    .then(function () {
                        if (seq !== state.seq) return;
                        state.coverage = null;
                        scheduleEstimate(0);
                        return loadSaves();
                    });
            }).catch(function () {});
        }

        // --- wiring -------------------------------------------------------
        var closeBtn = $('offline-save-close');
        if (closeBtn) closeBtn.addEventListener('click', close);
        var detail = $('offline-detail-select');
        if (detail) detail.addEventListener('change', function () { scheduleEstimate(ESTIMATE_DEBOUNCE_MS); });
        var saveBtn = $('offline-save-start');
        if (saveBtn) saveBtn.addEventListener('click', startSave);
        var panelEl = $('offline-save-panel');
        if (panelEl) panelEl.hidden = true;
        setExpanded(false);

        return {
            open: open,
            close: close,
            toggle: toggle,
            handleKeydown: handleKeydown,
            isOpen: function () { return state.open; },
            hasTimers: function () { return state.estTimer !== null || state.pollTimer !== null; },
            // for tests
            _startSave: startSave,
            _pollTick: pollTick,
            _deleteSave: deleteSave,
        };
    }

    root.NovaOfflineSavePanel = {
        create: create,
        DETAIL_CHOICES: DETAIL_CHOICES,
        offsetForValue: offsetForValue,
        statusText: statusText,
        reasonText: reasonText,
    };
})(typeof window !== 'undefined' ? window : this);
