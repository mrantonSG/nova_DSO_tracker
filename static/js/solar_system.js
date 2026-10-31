/*
 * solar_system.js — the Solar System page (templates/solar_system.html).
 *
 * Fetches /api/desktop_inputs for the selected location, builds the table with
 * NovaSolarRows and the status strip with NovaDashboardRows.statusStrip(), and draws
 * the selected body with SolarSystemChart. It recomputes every 60 s, live and in
 * simulation (the simulated instant is the sim date at the current wall clock, so it
 * moves too); the same tick moves the chart's Now line.
 *
 * Below the chart, the Rig fit card (NovaSolarRigFit) lists the user's active rigs
 * (solarRigs, embedded by core.py) for the selected body; it follows the table.
 *
 * The Calendar tab (NovaSolarCalendar) is built only while it is shown, and again only
 * when its location, threshold, sampling interval or observing night changes; the 60 s
 * tick otherwise costs one key comparison. Clicking an event opens the Bodies tab on
 * that body's Month view for the event's night (a conjunction's brighter body). The
 * filter above it (a body, "hide events that are not visible") only hides rendered
 * rows; it never recalculates.
 *
 * When the location's inputs do not load, both tabs show the translated error; the 60 s
 * tick keeps trying, and each tab clears its error on its next successful build.
 *
 * Shared with the dashboard on purpose: sessionStorage.selectedLocation and
 * POST /set_location for the location, localStorage.simModeActive and simDate for
 * simulation. Its own tab key is localStorage.nova_solar_tab; it never writes the
 * dashboard's activeTab, nova_last_tab or nova_desktop_cache_*. The calendar filter's
 * keys are localStorage.nova_solar_cal_body and nova_solar_cal_hide_muted.
 *
 * When /get_locations does not answer with JSON (a guest), the page uses the location
 * /api/desktop_inputs picks without a parameter and never POSTs /set_location.
 */
(function () {
    'use strict';

    const REFRESH_MS = 60000;
    const TAB_KEY = 'nova_solar_tab';
    const CAL_BODY_KEY = 'nova_solar_cal_body';
    const CAL_HIDE_KEY = 'nova_solar_cal_hide_muted';
    const AU_MILLION_KM = 149.5978707;
    const TABS = ['bodies', 'calendar'];
    const i18n = typeof solarI18n !== 'undefined' ? solarI18n : {};

    const state = {
        inputs: null,
        locName: null,
        fallback: false,        // no /get_locations: never POST /set_location
        result: null,
        selected: null,
        requested: null,        // ?body=
        view: 'day',
        chart: null,
        chartKey: null,         // what the chart shows, so a refresh redraws only on change
        rangeDate: null,        // the night the month/year controls were last set from
        tab: 'bodies',
        calKey: null,           // what the calendar table shows, so it is rebuilt only on change
        calResult: null,        // the last calendar, re-rendered when the filter changes
        calPending: false,      // a calendar build is waiting for the next paint
        calFilter: { body: '', hideMuted: false },
    };

    const $ = id => document.getElementById(id);

    function storageGet(store, key) {
        try { return window[store].getItem(key); } catch (e) { return null; }
    }

    function storageSet(store, key, value) {
        try { window[store].setItem(key, value); } catch (e) { /* private mode: not remembered */ }
    }

    function bodyName(body) {
        return (i18n.bodies && i18n.bodies[body]) || body;
    }

    /** A translated template with {key} placeholders filled in. */
    function fill(template, values) {
        let out = String(template === undefined ? '' : template);
        Object.keys(values).forEach(k => { out = out.split('{' + k + '}').join(String(values[k])); });
        return out;
    }

    /** '+4.6' or '−6.6' (U+2212). */
    function signed(x, digits) {
        return (x < 0 ? '−' : '+') + Math.abs(x).toFixed(digits);
    }

    function europeanDate(iso) {
        return typeof formatDateISOtoEuropean === 'function' ? formatDateISOtoEuropean(iso) : iso;
    }

    function simDate() {
        const toggle = $('sim-mode-toggle'), input = $('sim-date-input');
        return toggle && toggle.checked && input && input.value ? input.value : null;
    }

    function effectiveNow() {
        const loc = state.inputs && state.inputs.location;
        if (!loc) return Date.now();
        return NovaDashboardRows._wall.effectiveInstant(Date.now(), simDate(), String(loc.timezone), NovaAstro).utcMs;
    }

    // ------------------------------------------------------------------ errors

    /** The page error, or null to clear it. While it shows, the table and chart are
     *  hidden (solar_system.css makes [hidden] win) and the chart is dropped; the next
     *  successful refresh draws it again. */
    function showError(message) {
        const el = $('solar-error');
        el.textContent = message || '';
        el.hidden = !message;
        $('solar-table-wrapper').hidden = !!message;
        $('chart-section').hidden = !!message;
        $('solar-rigfit').hidden = !!message;
        if (message) resetChart();
    }

    /** The location's inputs did not load: both tabs say so. Each clears its own error
     *  on its next successful build (calKey null makes the calendar rebuild). */
    function showInputsError() {
        showError(i18n.inputsError);
        showCalError(i18n.inputsError);
        state.calKey = null;
    }

    function showChartError(message) {
        const el = $('solar-chart-error');
        el.textContent = message || '';
        el.hidden = !message;
        $('chart-area').hidden = !!message;
    }

    // ------------------------------------------------------------------ location

    async function fetchInputs(name) {
        const url = '/api/desktop_inputs' + (name ? '?location=' + encodeURIComponent(name) : '');
        const resp = await fetch(url);
        if (!resp.ok) throw new Error('desktop_inputs HTTP ' + resp.status);
        const inputs = await resp.json();
        if (!inputs || !inputs.location) throw new Error('desktop_inputs: no location');
        return inputs;
    }

    function fillSelect(names, selected) {
        const select = $('location-select');
        select.innerHTML = '';
        names.forEach(name => {
            const option = document.createElement('option');
            option.value = name;
            option.textContent = name;
            if (name === selected) option.selected = true;
            select.appendChild(option);
        });
        if (selected) select.value = selected;
    }

    /** The location list as the dashboard reads it, or null when there is none to read. */
    async function fetchLocationList() {
        try {
            const resp = await fetch('/get_locations');
            if (!resp.ok) return null;
            const data = await resp.json();   // a guest is redirected to the login page: not JSON
            if (!data || !Array.isArray(data.locations) || !data.locations.length) return null;
            let initial = storageGet('sessionStorage', 'selectedLocation');
            if (!initial || !data.locations.includes(initial)) {
                initial = data.selected;
                if (initial) storageSet('sessionStorage', 'selectedLocation', initial);
            }
            return initial ? { names: data.locations, selected: initial } : null;
        } catch (e) {
            return null;
        }
    }

    async function loadLocation() {
        const list = await fetchLocationList();
        if (list) {
            state.fallback = false;
            fillSelect(list.names, list.selected);
            state.inputs = await fetchInputs(list.selected);
        } else {
            state.fallback = true;
            state.inputs = await fetchInputs(null);
            fillSelect([state.inputs.location.name], state.inputs.location.name);
        }
        state.locName = state.inputs.location.name;
    }

    /** Remember the location, in this tab and (not as a guest) on the server. */
    function commitLocation(name) {
        storageSet('sessionStorage', 'selectedLocation', name);
        if (state.fallback) return;
        fetch('/set_location', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({location: name})
        }).then(r => r.json())
          .then(data => { if (data.status !== 'success') console.warn('Failed to persist location:', data.message); })
          .catch(err => console.warn('Failed to persist location:', err));
    }

    /** Switch only once the new location's inputs have loaded; on failure the page,
     *  the select and what is remembered all stay on the previous location. */
    async function onLocationChange() {
        const select = $('location-select');
        const name = select.value;
        const previous = state.locName;
        if (!name || name === previous) return;
        select.disabled = true;
        let inputs;
        try {
            inputs = await fetchInputs(name);
        } catch (e) {
            console.error('Solar System: could not load the location', e);
            select.value = previous;
            select.disabled = false;
            showInputsError();
            return;
        }
        state.inputs = inputs;
        state.locName = inputs.location.name;
        commitLocation(name);
        resetChart();
        select.disabled = false;
        refresh();
    }

    // ------------------------------------------------------------------ simulation

    function applySim(isSim) {
        const strip = document.querySelector('.status-strip');
        const title = $('simulated-title-text');
        $('sim-date-input').disabled = !isSim;
        if (strip) strip.classList.toggle('simulated', isSim);
        if (title) title.style.display = isSim ? 'inline' : 'none';
    }

    function initSim() {
        const toggle = $('sim-mode-toggle'), input = $('sim-date-input');
        const savedOn = storageGet('localStorage', 'simModeActive') === 'true';
        const savedDate = storageGet('localStorage', 'simDate');
        if (savedOn && savedDate) {
            toggle.checked = true;
            input.value = savedDate;
        } else {
            toggle.checked = false;
        }
        applySim(toggle.checked);

        toggle.addEventListener('change', () => {
            if (toggle.checked && !input.value) {
                const today = new Date();
                input.value = today.getFullYear() + '-' + String(today.getMonth() + 1).padStart(2, '0')
                    + '-' + String(today.getDate()).padStart(2, '0');
            }
            storageSet('localStorage', 'simModeActive', String(toggle.checked));
            storageSet('localStorage', 'simDate', input.value);
            applySim(toggle.checked);
            refresh(true);
        });
        input.addEventListener('change', () => {
            if (!toggle.checked) return;
            storageSet('localStorage', 'simDate', input.value);
            refresh(true);
        });
    }

    // ------------------------------------------------------------------ strip and table

    function renderStrip() {
        const strip = NovaDashboardRows.statusStrip({inputs: state.inputs, nowMs: Date.now(), simDate: simDate()});
        $('solar-time').textContent = strip.time;
        $('solar-date').textContent = europeanDate(strip.date);
        $('solar-phase').textContent = strip.phase.toFixed(1) + '%';
        $('solar-dusk').textContent = strip.astronomical_dusk;
        $('solar-dawn').textContent = strip.astronomical_dawn;
    }

    /** A cell of the solar table; its column is data-solar-col (static/css/solar_system.css). */
    function cell(tr, col, text) {
        const td = document.createElement('td');
        td.dataset.solarCol = col;
        if (text !== undefined) td.textContent = text;
        tr.appendChild(td);
        return td;
    }

    function line(td, className, text) {
        const span = document.createElement('span');
        span.className = className;
        span.textContent = text;
        td.appendChild(span);
        return span;
    }

    /** Diameter: one value, or "a / b" for a conjunction; Saturn's ring tilt as a second line. */
    function diameterCell(tr, diameters, ringTiltDeg) {
        const text = diameters.map(d => d.toFixed(1)).join(' / ');
        if (ringTiltDeg === null || ringTiltDeg === undefined) return cell(tr, 'diam', text);
        const td = cell(tr, 'diam');
        line(td, 'solar-time', text);
        line(td, 'solar-sub', fill(i18n.rings, {value: signed(ringTiltDeg, 1) + '°'})).title = i18n.ringTiltHelp || '';
        return td;
    }

    /** Rise or set: the time with its day suffix, or a word ("Always up", ...). */
    function riseSetCell(tr, col, time, day, row, notUpText) {
        const td = cell(tr, col);
        if (time) line(td, 'solar-time', NovaSolarRows.timeLabel(time, day));
        else if (notUpText) line(td, 'solar-note', notUpText);
        else if (row.alwaysUp) line(td, 'solar-note', i18n.alwaysUp);
        else if (row.neverUp) line(td, 'solar-note', i18n.neverUp);
        else td.textContent = '–';
        return td;
    }

    function renderTable() {
        const tbody = $('solar-body');
        tbody.innerHTML = '';
        const threshold = state.result.threshold;
        state.result.rows.forEach(row => {
            const tr = document.createElement('tr');
            tr.className = 'clickable-row';
            tr.dataset.body = row.body;
            if (row.muted) {
                tr.classList.add('geometrically-impossible');
                tr.title = i18n.belowThreshold;
            }
            if (row.body === state.selected) {
                tr.classList.add('active-project-row');
                tr.setAttribute('aria-selected', 'true');
            }

            cell(tr, 'body', bodyName(row.body));
            const alt = cell(tr, 'alt', row.altNow.toFixed(1));
            if (row.altNow >= threshold && !row.muted) {
                alt.classList.add('highlight');
                const badge = document.createElement('span');
                badge.className = 'alt-badge';
                badge.textContent = alt.textContent;
                alt.textContent = '';
                alt.appendChild(badge);
            }
            cell(tr, 'az', row.azNow.toFixed(1));
            riseSetCell(tr, 'rise', row.rise, row.riseDay, row, row.notUpTonight ? i18n.notUpTonight : null);
            const transit = cell(tr, 'transit');
            if (row.transit) {
                line(transit, 'solar-time', NovaSolarRows.timeLabel(row.transit, row.transitDay));
                line(transit, 'solar-sub', '(' + row.transitAlt.toFixed(1) + '°)');
            } else {
                transit.textContent = '–';
            }
            riseSetCell(tr, 'set', row.set, row.setDay, row, null);
            cell(tr, 'maxalt', row.maxAlt.toFixed(1));
            cell(tr, 'mag', row.mag.toFixed(1));
            diameterCell(tr, [row.diameterArcsec], row.ringTiltDeg);
            cell(tr, 'illum', row.illuminatedPct.toFixed(0));
            cell(tr, 'elong', row.elongationDeg.toFixed(1) + '° '
                + (row.elongationSide === 'E' ? i18n.east : i18n.west));

            tr.addEventListener('click', () => select(row.body));
            tbody.appendChild(tr);
        });
    }

    function select(body) {
        if (body === state.selected) return;
        state.selected = body;
        try {
            const url = new URL(window.location.href);
            url.searchParams.set('body', body);
            window.history.replaceState(null, '', url.toString());
        } catch (e) { /* the URL stays as it was */ }
        if (!state.result) return;
        renderTable();
        renderRigFit();
        showChart();
    }

    // ------------------------------------------------------------------ rig fit

    function span(parent, text, className) {
        const el = document.createElement('span');
        if (className) el.className = className;
        el.textContent = text;
        parent.appendChild(el);
        return el;
    }

    /** A value the rig lacks the data for: a dash, the missing field in its tooltip. */
    function missingSpan(parent, code, text) {
        const el = span(parent, text === undefined ? '–' : text, 'solar-missing');
        el.title = ((i18n.rigFit || {}).missing || {})[code] || '';
        return el;
    }

    function minSec(seconds) {
        const total = Math.round(seconds);
        return fill((i18n.rigFit || {}).minSec, {min: Math.floor(total / 60), sec: total % 60});
    }

    /** The Rig fit card for the selected body: one line per active rig, rig names as text only. */
    function renderRigFit() {
        const R = i18n.rigFit || {};
        const row = state.result && state.result.rows.find(r => r.body === state.selected);
        $('solar-rigfit').hidden = !row;
        if (!row) return;
        $('solar-rigfit-title').textContent = (R.title || '') + ' · ' + bodyName(row.body)
            + ' (' + row.diameterArcsec.toFixed(1) + '″)';
        const rigs = typeof solarRigs !== 'undefined' && Array.isArray(solarRigs) ? solarRigs : [];
        $('solar-rigfit-wrapper').hidden = !rigs.length;
        $('solar-rigfit-empty').hidden = rigs.length > 0;

        const extraKind = NovaSolarRigFit.ROTATION_HOURS[row.body] ? 'rotation' : (row.body === 'moon' ? 'mosaic' : null);
        const extra = $('solar-rigfit-extra');
        extra.hidden = !extraKind;
        $('solar-rigfit-extra-label').textContent = extraKind === 'rotation' ? R.rotationLimit
            : extraKind === 'mosaic' ? R.mosaic : '';
        extra.title = extraKind === 'rotation' ? R.rotationHelp : extraKind === 'mosaic' ? R.mosaicHelp : '';

        const tbody = $('solar-rigfit-body');
        tbody.innerHTML = '';
        rigs.forEach(rig => {
            const f = NovaSolarRigFit.fit(rig, row.body, row.diameterArcsec);
            const tr = document.createElement('tr');
            cell(tr, 'rig', f.name);

            const disc = cell(tr, 'disc');
            if (f.disc.ok) {
                span(disc, Math.round(f.disc.px) + ' px' + (f.disc.ringsPx === null ? ''
                    : ' · ' + fill(i18n.rings, {value: Math.round(f.disc.ringsPx) + ' px'})));
            } else missingSpan(disc, f.disc.missing);

            const fr = cell(tr, 'fratio');
            if (f.fRatio.ok) span(fr, 'f/' + f.fRatio.value.toFixed(1));
            else missingSpan(fr, f.fRatio.missing);
            span(fr, ' · ');
            if (f.ideal.ok) span(fr, fill(R.ideal, {ratio: f.ideal.value.toFixed(1)}));
            else missingSpan(fr, f.ideal.missing, fill(R.ideal, {ratio: '–'}));

            const barlow = cell(tr, 'barlow');
            if (f.barlow.ok) {
                span(barlow, f.barlow.kind === 'factor' ? f.barlow.factor + '×'
                    : f.barlow.kind === 'none' ? R.noneNeeded : R.aboveIdeal);
                barlow.title = fill(R.exactFactor, {factor: f.barlow.exact.toFixed(2) + '×'});
            } else missingSpan(barlow, f.barlow.missing);

            if (extraKind) {
                const td = cell(tr, 'extra');
                const v = extraKind === 'rotation' ? f.rotation : f.mosaic;
                if (!v.ok) missingSpan(td, v.missing);
                else if (extraKind === 'rotation') span(td, minSec(v.seconds));
                else span(td, v.panels === 1 ? R.fitsOneFrame : fill(R.panels, {n: v.panels, cols: v.cols, rows: v.rows}));
            }
            tbody.appendChild(tr);
        });
    }

    // ------------------------------------------------------------------ chart

    function resetChart() {
        if (state.chart) state.chart.destroy();
        state.chart = null;
        state.chartKey = null;
    }

    function ensureChart() {
        if (state.chart) return state.chart;
        const loc = state.inputs.location;
        state.chart = SolarSystemChart.create({
            canvas: $('solarChartCanvas'),
            tz: String(loc.timezone), locName: loc.name, lat: loc.lat, lon: loc.lon,
            threshold: loc.altitude_threshold, interval: loc.sampling_interval, mask: loc.horizon_mask,
            nowMs: effectiveNow, i18n: i18n, locale: document.documentElement.lang || 'en'
        });
        return state.chart;
    }

    function setViewButtons() {
        document.querySelectorAll('.view-buttons .view-button').forEach(btn => {
            const on = btn.dataset.view === state.view;
            btn.classList.toggle('active', on);
            btn.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
    }

    function showChart(force) {
        if (!state.result || !state.selected) return;
        const view = {
            body: state.selected, label: bodyName(state.selected), view: state.view,
            date: state.result.date,
            year: $('solar-year-select').value, month: $('solar-month-select').value
        };
        const key = JSON.stringify([state.locName, view.body, view.view, view.view === 'day' ? view.date : view.year,
                                    view.view === 'month' ? view.month : null]);
        if (!force && key === state.chartKey) return;
        $('solar-chart-title').textContent = view.label + ' – ' + i18n.chartTitle;
        try {
            ensureChart().show(view);
            state.chartKey = key;
            showChartError(null);
        } catch (e) {
            console.error('Solar System: could not draw the chart', e);
            resetChart();
            showChartError(i18n.chartError);
        }
    }

    function initChartControls() {
        document.querySelectorAll('.view-buttons .view-button').forEach(btn => {
            btn.addEventListener('click', () => {
                state.view = btn.dataset.view;
                setViewButtons();
                showChart();
            });
        });
        $('solar-month-select').addEventListener('change', () => showChart());
        $('solar-year-select').addEventListener('change', () => showChart());
        setViewButtons();
    }

    function syncRangeControls(date) {
        const [y, m] = date.split('-');
        $('solar-year-select').value = String(+y);
        $('solar-month-select').value = String(+m);
    }

    // ------------------------------------------------------------------ refresh

    /** Recompute everything; force redraws the chart even when it shows the same night
     *  (a simulation change). Any failure shows the translated error, never throws. */
    function refresh(force) {
        if (!state.inputs) return;
        try {
            state.result = NovaSolarRows.rows({inputs: state.inputs, nowMs: Date.now(), simDate: simDate()});
            renderStrip();
            showError(null);
            if (!state.selected) state.selected = NovaSolarRows.pickBody(state.result.rows, state.requested);
            // Only a new night moves the month/year controls: a failed refresh in between
            // leaves rangeDate alone, so the user's choice survives it.
            if (state.result.date !== state.rangeDate) {
                syncRangeControls(state.result.date);
                state.rangeDate = state.result.date;
            }
            renderTable();
            renderRigFit();
            showChart(force);
            if (state.chart) state.chart.updateNow();
        } catch (e) {
            console.error('Solar System: could not calculate', e);
            state.result = null;
            showError(i18n.error);
        }
        if (state.tab === 'calendar') renderCalendar();
    }

    // ------------------------------------------------------------------ calendar

    function showCalError(message) {
        const el = $('solar-cal-error');
        el.textContent = message || '';
        el.hidden = !message;
        $('solar-cal-wrapper').hidden = !!message;
    }

    /** '25', '0' or '−27' (U+2212, as timeLabel() prints a negative day). */
    function signedDays(n) {
        return n < 0 ? '−' + (-n) : String(n);
    }

    /** The filter: a body (a conjunction matches either of its two) and "hide muted". */
    function calRowShown(row) {
        if (state.calFilter.hideMuted && row.muted) return false;
        return !state.calFilter.body || row.bodies.indexOf(state.calFilter.body) >= 0;
    }

    function renderCalendarRows(result) {
        state.calResult = result;
        const tbody = $('solar-cal-body');
        tbody.innerHTML = '';
        const help = i18n.eventHelp || {};
        result.rows.filter(calRowShown).forEach(row => {
            const conj = row.kind === 'conjunction';
            const tr = document.createElement('tr');
            tr.className = 'clickable-row';
            tr.dataset.body = row.body;
            if (row.muted) {
                tr.classList.add('geometrically-impossible');
                tr.title = row.mutedReason === 'sun'
                    ? fill(i18n.tooCloseToSun, {elong: row.elongationDeg.toFixed(0)})
                    : i18n.belowThresholdNight;
            }
            // The night's date only; the event's own local time is the cell's tooltip
            const date = cell(tr, 'date', europeanDate(row.night));
            date.title = String(i18n.exactTime).replace('{time}', NovaSolarRows.timeLabel(row.hm, row.hmDay));
            cell(tr, 'body', row.bodies.map(bodyName).join(' – '));
            const event = cell(tr, 'event', conj ? fill(i18n.conjunction, {sep: row.sepDeg.toFixed(1)})
                                                 : (i18n[row.kind] || row.kind));
            const helpText = conj ? help[row.bodies.indexOf('moon') >= 0 ? 'conjunctionMoon' : 'conjunction'] : help[row.kind];
            const notes = [];
            if (helpText) notes.push(helpText);
            if (row.closestApproach) {
                notes.push(fill(i18n.closestApproach, {
                    date: europeanDate(row.closestApproach.night),
                    dist: (row.closestApproach.distAU * AU_MILLION_KM).toFixed(1),
                }));
            }
            if (notes.length) event.title = notes.join('\n');
            cell(tr, 'mag', conj ? row.mag.toFixed(1) + ' / ' + row.other.mag.toFixed(1) : row.mag.toFixed(1));
            diameterCell(tr, conj ? [row.diameterArcsec, row.other.diameterArcsec] : [row.diameterArcsec], row.ringTiltDeg);
            cell(tr, 'elong', row.elongationDeg === null ? '–' : row.elongationDeg.toFixed(1));
            cell(tr, 'maxalt', row.maxAlt.toFixed(1));
            cell(tr, 'days', signedDays(row.daysFromNow));
            tr.addEventListener('click', () => openBodyMonth(row.body, row.night));
            tbody.appendChild(tr);
        });
    }

    /** What the calendar depends on: location, its threshold and sampling, the night. */
    function calendarKey() {
        const loc = state.inputs.location;
        const wall = NovaDashboardRows._wall;
        const now = wall.effectiveInstant(Date.now(), simDate(), String(loc.timezone), NovaAstro);
        return JSON.stringify([state.locName, loc.altitude_threshold, loc.sampling_interval,
                               wall.observingNight(now.wall)]);
    }

    /** "Calculating..." in place of the table while a build is pending. */
    function showCalLoading(on) {
        $('solar-cal-loading').hidden = !on;
        $('solar-cal-wrapper').hidden = on;
    }

    /** Run fn once the browser has painted: a frame, then a task. */
    function afterPaint(fn) {
        if (typeof requestAnimationFrame === 'function') requestAnimationFrame(() => setTimeout(fn, 0));
        else setTimeout(fn, 0);
    }

    /** Rebuild the calendar when what it depends on changed. The build takes a few hundred
     *  ms, so "Calculating..." is painted first; one build is pending at a time and works
     *  from the inputs as they are when it runs. Any failure shows the translated error,
     *  never throws; the next tick tries again. */
    function renderCalendar() {
        if (!state.inputs || state.calPending) return;
        try {
            if (calendarKey() === state.calKey) return;
        } catch (e) {
            console.error('Solar System: could not calculate the calendar', e);
            state.calKey = null;
            showCalError(i18n.error);
            return;
        }
        state.calPending = true;
        showCalError(null);
        showCalLoading(true);
        afterPaint(buildCalendar);
    }

    function buildCalendar() {
        state.calPending = false;
        showCalLoading(false);
        try {
            const key = calendarKey();
            if (key === state.calKey) return;
            const result = NovaSolarCalendar.events({inputs: state.inputs, nowMs: Date.now(), simDate: simDate()});
            renderCalendarRows(result);
            showCalError(null);
            state.calKey = key;
        } catch (e) {
            console.error('Solar System: could not calculate the calendar', e);
            state.calKey = null;
            showCalError(i18n.error);
        }
    }

    /** The filter's controls, from and to localStorage; a change re-renders the last calendar. */
    function initCalFilter() {
        const select = $('solar-cal-body-filter'), hide = $('solar-cal-hide-muted');
        const saved = storageGet('localStorage', CAL_BODY_KEY);
        state.calFilter.body = NovaPlanets.BODIES.indexOf(saved) >= 0 ? saved : '';
        state.calFilter.hideMuted = storageGet('localStorage', CAL_HIDE_KEY) === 'true';
        select.value = state.calFilter.body;
        hide.checked = state.calFilter.hideMuted;
        const apply = () => { if (state.calResult) renderCalendarRows(state.calResult); };
        select.addEventListener('change', () => {
            state.calFilter.body = NovaPlanets.BODIES.indexOf(select.value) >= 0 ? select.value : '';
            storageSet('localStorage', CAL_BODY_KEY, state.calFilter.body);
            apply();
        });
        hide.addEventListener('change', () => {
            state.calFilter.hideMuted = !!hide.checked;
            storageSet('localStorage', CAL_HIDE_KEY, String(state.calFilter.hideMuted));
            apply();
        });
    }

    /** A calendar row: the Bodies tab, that body, the Month view of the event's night. */
    function openBodyMonth(body, night) {
        switchTab('bodies');
        state.view = 'month';
        setViewButtons();
        syncRangeControls(night);
        if (body !== state.selected) select(body);
        else showChart(true);
        $('chart-section').scrollIntoView({behavior: 'smooth', block: 'start'});
    }

    // ------------------------------------------------------------------ tabs

    function switchTab(name) {
        if (TABS.indexOf(name) < 0) name = 'bodies';
        state.tab = name;
        storageSet('localStorage', TAB_KEY, name);
        document.querySelectorAll('.tab-container .tab-button').forEach(btn => {
            const on = btn.dataset.tab === name;
            btn.classList.toggle('active', on);
            btn.setAttribute('aria-selected', on ? 'true' : 'false');
        });
        TABS.forEach(tab => $(tab + '-tab').classList.toggle('active', tab === name));
        if (name === 'calendar') renderCalendar();
        // A chart drawn while its tab was hidden has no size yet: fit it to its card now
        else if (state.chart) state.chart.resize();
    }

    function initTabs() {
        // The key is the page's own, never the dashboard's.
        document.querySelectorAll('.tab-container .tab-button').forEach(btn => {
            btn.addEventListener('click', () => switchTab(btn.dataset.tab));
        });
        switchTab(storageGet('localStorage', TAB_KEY));
    }

    /** Load the location; on failure both tabs show the error and the next tick tries again. */
    async function loadInputs() {
        try {
            await loadLocation();
            return true;
        } catch (e) {
            console.error('Solar System: could not load location data', e);
            showInputsError();
            return false;
        }
    }

    /** The 60 s tick: while no inputs have loaded, load them first. */
    async function tick() {
        if (!state.inputs && !(await loadInputs())) return;
        refresh();
    }

    async function init() {
        const params = new URLSearchParams(window.location.search);
        state.requested = params.get('body');
        initCalFilter();
        initTabs();
        initSim();
        initChartControls();
        $('location-select').addEventListener('change', onLocationChange);
        if (await loadInputs()) refresh();
        setInterval(tick, REFRESH_MS);
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
