/*
 * solar_system.js — the Solar System page (templates/solar_system.html).
 *
 * Fetches /api/desktop_inputs for the selected location, builds the table with
 * NovaSolarRows and the status strip with NovaDashboardRows.statusStrip(), and draws
 * the selected body with SolarSystemChart. It recomputes every 60 s, live and in
 * simulation (the simulated instant is the sim date at the current wall clock, so it
 * moves too); the same tick moves the chart's Now line.
 *
 * Below the chart, the planetary calculator (NovaSolarRigFit) works out the selected
 * body for one of the user's telescopes and cameras (solarEquipment, embedded by
 * core.py) and an amplification; it follows the table. Under its summary, the seeing
 * outlook (NovaSolarSeeing, tiles with NovaWeatherIcons' seeing icons) from
 * /api/upper_wind, fetched after the page has rendered and never waited for; shown with
 * or without equipment, hidden on any error or without data.
 *
 * While the Moon is selected, the Moon card between the chart and the calculator
 * (NovaSolarMoon, when nova_solar_moon.js is loaded): the Moon drawn as tonight at its
 * highest (lit part, terminator, numbered markers for tonight's terminator features,
 * the libration arrow), tiles for colongitude, libration and illumination, the
 * terminator features with their next good nights in the tooltip, and the libration
 * line, and the feature names' source. Computed once per location, threshold, sampling
 * interval and night, like the calendar; a view change (normal, star diagonal, Newtonian)
 * only redraws, and the 60 s tick leaves the card's nodes (and the focus) alone unless
 * the night, location, simulated date, view or language changed.
 *
 * Two cards of the same kind stand beside it, one body each, shown while that body is
 * selected: Jupiter (NovaSolarJupiter) draws the system at one fixed scale -- the disc in
 * the centre, the four Galilean moons by what each is doing (beside or in front of the
 * disc, hidden behind it, eclipsed), the shadows they throw, the Great Red Spot while it
 * faces us, and the way celestial north lies; its names are set at a fixed size and
 * staggered so they never overlap, and the strip is redrawn when its width changes --
 * with a tile for the Great Red Spot's predicted transits (the longitude editable, kept
 * under nova_solar_grs) and one for the System II central meridian, tonight's transits,
 * occultations, eclipses and shadow transits (each at its own local date and time, with
 * its length and altitude, and a badge on a double one) and the next nights' shadow
 * transits; Mars (NovaSolarMars) draws the disc as the eyepiece has it -- the polar cap
 * tilted towards us, the shaded part and terminator, a dot per named albedo feature and
 * the pole it leans by -- with a tile for the central meridian (De and Ds in words in its
 * tooltip) and one for the best time tonight, and the features on the disc ranked, each
 * named with a bar for how close it is to the central meridian, the rest in its tooltip.
 * Both follow the Moon's rule exactly: computed once per night, memoised on the same key
 * plus the view and the language, the tick otherwise costs one string comparison, and
 * their own failures show in their own card.
 *
 * The Calendar tab (NovaSolarCalendar, plus NovaSolarMoon's Lunar X and libration rows
 * and NovaSolarJupiter's Galilean rows when they are loaded; if those fail, the planets'
 * rows still show) is built only while it
 * is shown, and again only
 * when its location, threshold, sampling interval or observing night changes; the 60 s
 * tick otherwise costs one key comparison. Clicking an event opens the Bodies tab on
 * that body's Month view for the event's night (a conjunction's brighter body). The
 * filter above it (a body, an event type, "hide events that are not visible") only
 * hides rendered rows; it never recalculates.
 *
 * When the location's inputs do not load, both tabs show the translated error; the 60 s
 * tick keeps trying, and each tab clears its error on its next successful build.
 *
 * Shared with the dashboard on purpose: sessionStorage.selectedLocation and
 * POST /set_location for the location, localStorage.simModeActive and simDate for
 * simulation. Its own tab key is localStorage.nova_solar_tab; it never writes the
 * dashboard's activeTab, nova_last_tab or nova_desktop_cache_*. The calendar filter's
 * keys are localStorage.nova_solar_cal_body, nova_solar_cal_type and
 * nova_solar_cal_hide_muted; the calculator's nova_solar_calc_telescope and
 * nova_solar_calc_camera (component record_uids) and nova_solar_calc_amp (stored only
 * once the user picks one); the three detail cards' one shared view nova_solar_view;
 * the Jupiter card's own Great Red Spot longitude nova_solar_grs ({ lon, date }).
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
    const CAL_TYPE_KEY = 'nova_solar_cal_type';
    const MOON_VIEW_KEY = 'nova_solar_moon_view';
    // The three detail cards share one view. It was the Moon's own key while the Moon was
    // the only card; the old value is adopted on the first load after the change.
    const VIEW_KEY = 'nova_solar_view';
    // The Calendar's event types and the kinds of row each one shows
    const CAL_TYPES = {
        planets: ['opposition', 'elongationEast', 'elongationWest'],
        conjunctions: ['conjunction'],
        lunar: ['lunarX', 'libration'],
        galilean: ['galilean'],
    };
    // A feature's Sun-altitude bar is full at this many degrees
    const MOON_BAR_DEG = 15;
    const CALC_TELESCOPE_KEY = 'nova_solar_calc_telescope';
    const CALC_CAMERA_KEY = 'nova_solar_calc_camera';
    const CALC_AMP_KEY = 'nova_solar_calc_amp';
    const AU_MILLION_KM = 149.5978707;
    // The server caches the upper wind 3 h; asking hourly picks up a new copy in time
    const WIND_REFETCH_MS = 3600000;
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
        calFilter: { body: '', type: '', hideMuted: false },
        // The detail cards: tonight's result and the card's own look-ahead lists, for the
        // key (the calendar's: location, threshold, sampling, night); the view they are
        // all drawn in
        moon: { key: null, tonight: null, next: null },
        jupiter: { key: null, tonight: null, next: null, grs: null, grsKey: null },
        // The user's own Great Red Spot longitude ({ lon, date }, localStorage nova_solar_grs),
        // or null for the shipped default; the width the Jupiter strip was last drawn at
        grsOverride: null,
        jupiterWidth: 0,
        mars: { key: null, tonight: null },
        cardView: 'normal',
        // The drawings' top layers and their markers by chip number, for showHotMarker()
        moonHot: { layer: null, markers: {} },
        marsHot: { layer: null, markers: {} },
        // What each card was last drawn for (its render's key), so a tick redraws nothing
        moonDrawn: null,
        jupiterDrawn: null,
        marsDrawn: null,
        // The calculator's choice: component records, and the amplification the user
        // picked (null: the one closest to the ideal focal ratio)
        calc: { telescope: null, camera: null, amp: null },
        // The jet stream wind (/api/upper_wind) of the location `key` ('lat,lon'), fetched
        // at `at`; null data: none, or the fetch failed (no seeing row). `pending`: the
        // key being fetched. `memo`: the last forecast and what it was computed from.
        wind: { key: null, data: null, at: 0, pending: null },
        seeingMemo: { key: null, value: null },
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

    /** '12' or '−3' (U+2212): whole degrees. */
    function wholeDeg(x) {
        const n = Math.round(x);
        return n < 0 ? '−' + (-n) : String(n);
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
        $('solar-calc').hidden = !!message;
        if (message) {
            $('solar-moon').hidden = true;
            resetChart();
        }
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
        renderMoon();
        renderJupiter();
        renderMars();
        renderCalc();
        showChart();
    }

    // ------------------------------------------------------------------ planetary calculator

    function span(parent, text, className) {
        const el = document.createElement('span');
        if (className) el.className = className;
        el.textContent = text;
        parent.appendChild(el);
        return el;
    }

    /** A value the telescope or camera lacks the data for: a dash, the missing field in its tooltip. */
    function missingSpan(parent, code, text) {
        const el = span(parent, text === undefined ? '–' : text, 'solar-missing');
        el.title = ((i18n.calc || {}).missing || {})[code] || '';
        return el;
    }

    function minSec(seconds) {
        const total = Math.round(seconds);
        return fill((i18n.calc || {}).minSec, {min: Math.floor(total / 60), sec: total % 60});
    }

    function equipment() {
        const eq = typeof solarEquipment !== 'undefined' && solarEquipment ? solarEquipment : {};
        return {
            telescopes: Array.isArray(eq.telescopes) ? eq.telescopes : [],
            cameras: Array.isArray(eq.cameras) ? eq.cameras : [],
        };
    }

    /** The amplification in use: the user's pick, else the one closest to the ideal. */
    function calcAmp() {
        return state.calc.amp !== null ? state.calc.amp
            : NovaSolarRigFit.bestAmplification(state.calc.telescope, state.calc.camera);
    }

    function fillCalcSelect(select, records) {
        select.innerHTML = '';
        records.forEach(r => {
            const option = document.createElement('option');
            option.value = r.uid;
            option.textContent = String(r.name === undefined || r.name === null ? '' : r.name);
            select.appendChild(option);
        });
    }

    /** The calculator's selects, from solarEquipment and localStorage: a stored uid that is
     *  no longer there, or a stored amplification not in the list, gives the default. */
    function initCalc() {
        const eq = equipment();
        if (!eq.telescopes.length || !eq.cameras.length) return;
        const C = i18n.calc || {};
        const byUid = (list, uid) => list.find(r => r && uid && r.uid === uid) || null;
        state.calc.telescope = byUid(eq.telescopes, storageGet('localStorage', CALC_TELESCOPE_KEY))
            || NovaSolarRigFit.defaultTelescope(eq.telescopes);
        state.calc.camera = byUid(eq.cameras, storageGet('localStorage', CALC_CAMERA_KEY))
            || NovaSolarRigFit.defaultCamera(eq.cameras);
        const storedAmp = Number(storageGet('localStorage', CALC_AMP_KEY));
        state.calc.amp = NovaSolarRigFit.AMPLIFICATIONS.indexOf(storedAmp) >= 0 ? storedAmp : null;

        const tel = $('solar-calc-telescope'), cam = $('solar-calc-camera'), amp = $('solar-calc-amp');
        fillCalcSelect(tel, eq.telescopes);
        fillCalcSelect(cam, eq.cameras);
        amp.innerHTML = '';
        NovaSolarRigFit.AMPLIFICATIONS.forEach(a => {
            const option = document.createElement('option');
            option.value = String(a);
            option.textContent = a === 1 ? (C.none || '') + ' (1×)' : a + '×';
            amp.appendChild(option);
        });
        const sync = () => {
            tel.value = state.calc.telescope.uid;
            cam.value = state.calc.camera.uid;
            amp.value = String(calcAmp());
        };
        sync();

        tel.addEventListener('change', () => {
            state.calc.telescope = byUid(eq.telescopes, tel.value) || state.calc.telescope;
            storageSet('localStorage', CALC_TELESCOPE_KEY, state.calc.telescope.uid);
            sync();
            renderCalc();
        });
        cam.addEventListener('change', () => {
            state.calc.camera = byUid(eq.cameras, cam.value) || state.calc.camera;
            storageSet('localStorage', CALC_CAMERA_KEY, state.calc.camera.uid);
            sync();
            renderCalc();
        });
        amp.addEventListener('change', () => {
            const value = Number(amp.value);
            if (NovaSolarRigFit.AMPLIFICATIONS.indexOf(value) >= 0) {
                state.calc.amp = value;
                storageSet('localStorage', CALC_AMP_KEY, String(value));
            }
            sync();
            renderCalc();
        });
    }

    /** A short muted line under a value that says what it means (none without text, unless
     *  `keep`: then an empty one that holds its place). */
    function hint(dd, text, keep) {
        if (!text && !keep) return null;
        const el = document.createElement('div');
        el.className = 'solar-calc-hint';
        el.textContent = text || '';
        dd.appendChild(el);
        return el;
    }

    /** One line of the result: the label (its tooltip, if any) and the value's element. */
    function calcLine(dl, key, label, help) {
        const row = document.createElement('div');
        row.className = 'solar-calc-row';
        row.dataset.calc = key;
        const dt = document.createElement('dt');
        dt.textContent = label || '';
        if (help) dt.title = help;
        const dd = document.createElement('dd');
        row.appendChild(dt);
        row.appendChild(dd);
        dl.appendChild(row);
        return dd;
    }

    /** The calculator's first line: "<Level> setup for <Body>." (the level in bold, its
     *  criteria in its tooltip) and the most useful next step. */
    function renderSummary(f, row) {
        const S = (i18n.calc || {}).summary || {};
        const el = $('solar-calc-summary');
        el.innerHTML = '';
        const best = NovaSolarRigFit.bestAmplification(state.calc.telescope, state.calc.camera);
        const sum = NovaSolarRigFit.summary(f, row.maxAlt, best, calcAmp());
        const body = bodyName(row.body);
        // The translation places {level}; the level word goes in between as its own element
        const parts = fill(S.sentence, {body: body}).split('{level}');
        span(el, parts[0]);
        const level = document.createElement('strong');
        level.className = 'solar-calc-level';
        level.dataset.level = sum.level;
        level.textContent = (S.level || {})[sum.level] || '';
        level.title = fill(S.levelHelp, {alt: NovaSolarRigFit.SUMMARY_MIN_ALTITUDE});
        el.appendChild(level);
        span(el, parts.slice(1).join(''));

        const step = sum.step;
        let next = '';
        if (!step) next = '';
        else if (step.kind === 'low') next = fill(S.low, {body: body, alt: Math.round(step.altitude)});
        else if (step.kind === 'amp') next = fill(S.amp, {amp: step.amp + '×'});
        else if (step.kind === 'bestAlready') next = fill(S.bestAlready, {body: body});
        else if (step.kind === 'smallDisc') next = fill(S.smallDisc, {body: body});
        else next = step.detail === 'fine' ? S.fine : step.detail === 'main' ? S.main : S.moon;
        if (next) span(el, ' ' + next, 'solar-calc-next');
    }

    // ------------------------------------------------------------------ seeing

    function windKey(loc) {
        return loc.lat + ',' + loc.lon;
    }

    /** Ask for the location's upper wind after the page has rendered: never waited for,
     *  asked again hourly, and any failure only hides the seeing line. */
    function loadWind() {
        const loc = state.inputs && state.inputs.location;
        if (!loc || typeof NovaSolarSeeing === 'undefined') return;
        const key = windKey(loc);
        if (state.wind.pending === key) return;
        if (state.wind.key === key && Date.now() - state.wind.at < WIND_REFETCH_MS) return;
        state.wind.pending = key;
        fetch('/api/upper_wind?lat=' + encodeURIComponent(loc.lat) + '&lon=' + encodeURIComponent(loc.lon))
            .then(resp => {
                if (!resp.ok) throw new Error('upper_wind HTTP ' + resp.status);
                return resp.json();
            })
            .then(data => (data && Array.isArray(data.times) && data.times.length ? data : null), err => {
                console.warn('Solar System: no upper wind for the seeing forecast', err);
                return null;
            })
            .then(data => {
                if (state.wind.pending === key) state.wind.pending = null;
                const now = state.inputs && state.inputs.location;
                if (!now || windKey(now) !== key) return;   // the location changed meanwhile
                state.wind = { key: key, data: data, at: Date.now(), pending: state.wind.pending };
                renderSeeing();
            });
    }

    /** 'Sat 31' in the page's language, the night's date. */
    function shortDate(date) {
        const [y, m, d] = date.split('-').map(Number);
        try {
            return new Intl.DateTimeFormat(document.documentElement.lang || 'en',
                {weekday: 'short', day: 'numeric', timeZone: 'UTC'}).format(new Date(Date.UTC(y, m - 1, d)));
        } catch (e) {
            return europeanDate(date);
        }
    }

    // The Day chart's seeing icon for each rating, by 7Timer's scale (1 best):
    // steady 1 (seeingExc), average 4 (seeingAvg), poor 7 (seeingPoor)
    const SEEING_ICON_LEVEL = { steady: 1, average: 4, poor: 7 };

    /** The rating's icon from NovaWeatherIcons as inline SVG in currentColor, or a plain
     *  dash without a rating (or without the icons). */
    function seeingIcon(parent, level) {
        const W = typeof NovaWeatherIcons !== 'undefined' ? NovaWeatherIcons : null;
        const icon = W && level ? W.ICONS[W.seeingIcon(SEEING_ICON_LEVEL[level])] : null;
        const box = document.createElement('span');
        box.className = 'solar-seeing-icon';
        if (!icon) {
            box.textContent = '–';
        } else {
            const svg = svgEl(box, 'svg', {viewBox: icon.viewBox, 'aria-hidden': 'true', focusable: 'false'});
            // The seeing icons are filled bars only, no cutouts
            icon.shapes.forEach(sh => svgEl(svg, 'path', {d: sh.d, fill: sh.fill, opacity: sh.opacity}));
        }
        parent.appendChild(box);
    }

    /** One night's tile: the day label, then the rating's icon (a dash without one). */
    function seeingTile(tiles, day, level) {
        const tile = document.createElement('div');
        tile.className = 'solar-seeing-tile';
        span(tile, day, 'solar-seeing-day');
        seeingIcon(tile, level);
        tiles.appendChild(tile);
        return tile;
    }

    /** The seeing outlook under the summary, for the selected body: one tile per night,
     *  coloured by its rating, tonight emphasised, the nights beyond 3 days faded. Shown
     *  with or without a telescope and camera. Until the location's wind has been
     *  answered, the row holds its place with invisible tiles, so nothing below it moves
     *  when the data arrives; without wind it is hidden. A simulated night outside the
     *  forecast says so instead of tiles, whether or not the body is up that night. */
    function renderSeeing() {
        const box = $('solar-seeing');
        const S = i18n.seeing || {};
        const L = S.level || {};
        const loc = state.inputs && state.inputs.location;
        const row = state.result && state.result.rows.find(r => r.body === state.selected);
        const w = state.wind;
        const tiles = $('solar-seeing-tiles'), none = $('solar-seeing-none');
        if (!row || !loc || typeof NovaSolarSeeing === 'undefined') {
            box.hidden = true;
            return;
        }
        if (w.key !== windKey(loc)) {
            // Not answered yet: the final row's height, invisible (solar-seeing-pending)
            box.hidden = false;
            box.classList.add('solar-seeing-pending');
            none.hidden = true;
            none.textContent = '';
            tiles.hidden = false;
            tiles.innerHTML = '';
            for (let i = 0; i < NovaSolarSeeing.OUTLOOK_NIGHTS; i++) seeingTile(tiles, '\u00a0', null);
            return;
        }
        box.classList.remove('solar-seeing-pending');
        if (!w.data) {
            box.hidden = true;
            return;
        }
        const memoKey = JSON.stringify([w.key, w.at, row.body, state.result.date, row.transitUtc, row.muted,
                                        loc.altitude_threshold, loc.sampling_interval]);
        let f = state.seeingMemo.key === memoKey ? state.seeingMemo.value : null;
        if (!f) {
            try {
                f = NovaSolarSeeing.forecast({inputs: state.inputs, body: row.body, date: state.result.date,
                                              wind: w.data, tonight: row});
            } catch (e) {
                console.error('Solar System: could not rate the seeing', e);
                box.hidden = true;
                return;
            }
            state.seeingMemo = { key: memoKey, value: f };
        }

        tiles.innerHTML = '';
        const outside = !!simDate() && f.tonight.reason === 'nodata';
        box.hidden = !outside && !f.outlook.length;
        none.hidden = !outside;
        none.textContent = outside ? (S.noForecast || '') : '';
        tiles.hidden = outside;
        if (outside) return;

        f.outlook.forEach(n => {
            const r = n.rating;
            const tonight = n.date === state.result.date;
            const day = tonight ? (S.tonight || '') : shortDate(n.date);
            const tile = seeingTile(tiles, day, r.level);
            tile.className = 'solar-seeing-tile' + (tonight ? ' solar-seeing-tile-tonight' : '')
                + (n.reliable ? '' : ' solar-seeing-tile-later');
            tile.dataset.date = n.date;
            tile.dataset.level = r.level || '';
            tile.setAttribute('role', 'listitem');
            tile.setAttribute('tabindex', '0');
            // The night's line (tonight's own wording, a later night named by its day
            // label) and the explanation; without a rating, why (under the threshold all
            // night: the Bodies table's and the calendar's words; above it only between
            // two whole hours: briefly) and the source. Then why a late night is faded.
            const tip = [];
            let label;
            if (r.level) {
                label = L[r.level] || '';
                tip.push(fill(tonight ? S.line : S.lineDay, {day: day, speed: Math.round(r.median), level: label}));
                tip.push(fill(S.help, {body: bodyName(row.body), max: Math.round(r.max)}));
            } else {
                label = (r.reason === 'muted' ? (tonight ? i18n.belowThreshold : i18n.belowThresholdNight)
                    : S.briefly) || '';
                tip.push(label);
                tip.push(S.source || '');
            }
            tile.setAttribute('aria-label', label + ', ' + day);
            if (!n.reliable) tip.push(S.lessReliable || '');
            tile.title = tip.filter(Boolean).join('\n');
        });
    }

    const SVG_NS = 'http://www.w3.org/2000/svg';
    // The preview's box, width over height (solar_system.css .solar-preview-figure,
    // aspect-ratio 3 / 2): the same for every camera, so the card's height never follows
    // the sensor's shape and the page does not move when the camera changes
    const PREVIEW_BOX_ASPECT = 3 / 2;

    function svgEl(parent, tag, attrs) {
        const el = document.createElementNS(SVG_NS, tag);
        Object.keys(attrs || {}).forEach(k => el.setAttribute(k, String(attrs[k])));
        parent.appendChild(el);
        return el;
    }

    /** The scale preview (NovaSolarRigFit.preview): the sensor with the body at exact scale
     *  as an inline SVG in a fixed 3:2 box, drawn from its top-left corner at its own aspect
     *  ratio, its labels in HTML over it (a fixed text size at any drawing size), and the ROI
     *  hint directly under the frame: in the box's empty space when the drawing is wider than
     *  3:2, else in the slot under the box. Box and slot are always there, also around the
     *  muted line that replaces the drawing without the data, so the card's height never
     *  changes with the camera. */
    function renderPreview(row) {
        const C = i18n.calc || {};
        const P = C.preview || {};
        const box = $('solar-calc-preview');
        box.innerHTML = '';
        const figure = document.createElement('div');
        figure.className = 'solar-preview-figure';
        box.appendChild(figure);
        // The ROI hint's slot keeps its two lines whether there is a hint or not
        const slot = document.createElement('div');
        slot.className = 'solar-preview-roi-slot';
        box.appendChild(slot);

        const g = NovaSolarRigFit.preview(state.calc.telescope, state.calc.camera, calcAmp(), row.body,
            row.diameterArcsec, row.illuminatedPct / 100, row.ringTiltDeg);
        if (!g.ok) {
            const p = document.createElement('p');
            p.className = 'solar-calc-preview-missing';
            const reason = (C.missing || {})[g.missing] || '';
            // An implausible sensor size says only what to check; anything else names the gap
            p.textContent = g.missing === 'sensorImplausible' ? reason : (P.none || '') + ' ' + reason;
            figure.appendChild(p);
            return;
        }
        const d = g.disc;
        // preview's viewBox keeps 5 % of the drawing's width on each side; without it the
        // frame starts at the top-left of the figure, level with the summary and the ROI
        // hint (the strokes on the edge overflow the SVG, solar_system.css)
        const pad = g.viewBox.w * 0.05 / 1.1;
        const vb = {x: g.viewBox.x + pad, y: g.viewBox.y + pad, w: g.viewBox.w - 2 * pad, h: g.viewBox.h - 2 * pad};
        // The drawing fills the box's width or its height (preserveAspectRatio xMinYMin meet):
        // the share of the box it takes on each axis places the labels
        const aspect = vb.w / vb.h;
        const fillW = aspect >= PREVIEW_BOX_ASPECT ? 1 : aspect / PREVIEW_BOX_ASPECT;
        const fillH = aspect >= PREVIEW_BOX_ASPECT ? PREVIEW_BOX_ASPECT / aspect : 1;
        const pct = (v, from, size, share) => (Math.round((v - from) / size * share * 1e6) / 1e4) + '%';
        const label = (className, text, x, y) => {
            const el = document.createElement('span');
            el.className = className;
            el.textContent = text;
            el.style.left = pct(x, vb.x, vb.w, fillW);
            el.style.top = pct(y, vb.y, vb.h, fillH);
            figure.appendChild(el);
            return el;
        };

        const svg = svgEl(figure, 'svg', {
            viewBox: [vb.x, vb.y, vb.w, vb.h].join(' '), preserveAspectRatio: 'xMinYMin meet',
            role: 'img', class: 'solar-calc-svg',
            'aria-label': fill(P.aria, {w: g.sensor.w, h: g.sensor.h, body: bodyName(row.body), disc: Math.round(d.px)}),
        });
        svgEl(svg, 'rect', {x: 0, y: 0, width: g.sensor.w, height: g.sensor.h, class: 'solar-preview-sensor'});
        if (g.mosaic) {
            g.mosaic.frames.forEach(fr => svgEl(svg, 'rect',
                {x: fr.x, y: fr.y, width: fr.w, height: fr.h, class: 'solar-preview-frame'}));
        }
        if (g.roi) svgEl(svg, 'rect', {x: g.roi.x, y: g.roi.y, width: g.roi.w, height: g.roi.h, class: 'solar-preview-roi'});

        const body = svgEl(svg, 'g', {class: 'solar-preview-body', 'data-body': row.body});
        if (g.rings) svgEl(body, 'path', {d: g.rings.back, class: 'solar-preview-ring'});
        if (g.phase) {
            svgEl(body, 'circle', {cx: d.cx, cy: d.cy, r: d.r, class: 'solar-preview-unlit'});
            svgEl(body, 'path', {d: g.phase.path, class: 'solar-preview-lit'});
            svgEl(body, 'title', {}).textContent = P.phaseHelp || '';
        } else {
            svgEl(body, 'circle', {cx: d.cx, cy: d.cy, r: d.r, class: 'solar-preview-lit'});
        }
        if (g.rings) svgEl(body, 'path', {d: g.rings.front, class: 'solar-preview-ring'});

        // The sensor's pixels at its top-left corner; "not to scale" under the enlarged disc
        label('solar-preview-label', g.sensor.w + ' × ' + g.sensor.h + ' px', 0, 0);
        if (d.notToScale) {
            label('solar-preview-label solar-preview-note', P.notToScale || '',
                d.cx, d.cy + (g.rings ? Math.max(d.r, g.rings.ry) : d.r));
        }
        if (g.roi) {
            const hintEl = document.createElement('div');
            hintEl.className = 'solar-calc-hint solar-calc-roi';
            hintEl.textContent = fill(P.roi, {w: g.roi.w, h: g.roi.h});
            hintEl.title = P.roiHelp || '';
            // As wide as the frame, under it: in the box when the frame leaves space below it
            // (any overflow runs into the slot's reserved lines), else in the slot
            hintEl.style.width = (Math.round(fillW * 1e6) / 1e4) + '%';
            if (fillH < 1) {
                hintEl.className += ' solar-calc-roi-inside';
                hintEl.style.top = (Math.round(fillH * 1e6) / 1e4) + '%';
                figure.appendChild(hintEl);
            } else {
                slot.appendChild(hintEl);
            }
        }
    }

    /** The planetary calculator for the selected body and the chosen telescope, camera and
     *  amplification: a one-line summary, the scale preview, then each value with a hint
     *  under it; without a telescope or a camera, the line to the Configuration page. */
    function renderCalc() {
        const C = i18n.calc || {};
        const H = C.hint || {};
        const row = state.result && state.result.rows.find(r => r.body === state.selected);
        $('solar-calc').hidden = !row;
        if (!row) return;
        $('solar-calc-title').textContent = (C.title || '') + ' · ' + bodyName(row.body)
            + ' (' + row.diameterArcsec.toFixed(1) + '″)';
        const ready = !!(state.calc.telescope && state.calc.camera);
        $('solar-calc-controls').hidden = !ready;
        $('solar-calc-summary').hidden = !ready;
        $('solar-calc-preview').hidden = !ready;
        $('solar-calc-result').hidden = !ready;
        $('solar-calc-empty').hidden = ready;
        const dl = $('solar-calc-result');
        dl.innerHTML = '';
        renderSeeing();   // with or without a telescope and camera
        if (!ready) return;

        const f = NovaSolarRigFit.fit(state.calc.telescope, state.calc.camera, calcAmp(), row.body, row.diameterArcsec);
        renderSummary(f, row);
        renderPreview(row);

        const disc = calcLine(dl, 'disc', C.disc, C.discHelp);
        if (f.disc.ok) {
            span(disc, Math.round(f.disc.px) + ' px' + (f.disc.ringsPx === null ? ''
                : ' · ' + fill(i18n.rings, {value: Math.round(f.disc.ringsPx) + ' px'})));
        } else missingSpan(disc, f.disc.missing);
        hint(disc, H.disc);

        const fr = calcLine(dl, 'fratio', C.focalRatio, C.focalRatioHelp);
        if (f.fRatio.ok) span(fr, 'f/' + f.fRatio.value.toFixed(1));
        else missingSpan(fr, f.fRatio.missing);
        span(fr, ' · ');
        if (f.ideal.ok) span(fr, fill(C.ideal, {ratio: f.ideal.value.toFixed(1)}));
        else missingSpan(fr, f.ideal.missing, fill(C.ideal, {ratio: '–'}));
        if (f.status.ok) span(fr, ' · ' + ((C.status || {})[f.status.value] || ''), 'solar-calc-status');
        // Always there and two lines high (solar_system.css), so a status change, or none,
        // never changes the card's height
        hint(fr, f.status.ok ? H[f.status.value] : '', true).className += ' solar-calc-hint-fratio';

        if (f.detail) {
            const dd = calcLine(dl, 'detail', C.detail, C.detailHelp);
            if (f.detail.ok) {
                span(dd, ((C.detailValue || {})[f.detail.value] || '') + ' · '
                    + fill(C.detailPoints, {n: Math.round(f.detail.elements)}));
            } else missingSpan(dd, f.detail.missing);
            hint(dd, H.detail);
        }
        if (f.rotation) {
            const dd = calcLine(dl, 'rotation', C.rotationLimit, C.rotationHelp);
            if (f.rotation.ok) span(dd, minSec(f.rotation.seconds));
            else missingSpan(dd, f.rotation.missing);
            hint(dd, H.rotation);
        }
        if (f.mosaic) {
            const dd = calcLine(dl, 'mosaic', C.mosaic, C.mosaicHelp);
            if (!f.mosaic.ok) missingSpan(dd, f.mosaic.missing);
            else span(dd, f.mosaic.panels === 1 ? C.fitsOneFrame
                : fill(C.panels, {n: f.mosaic.panels, cols: f.mosaic.cols, rows: f.mosaic.rows}));
            hint(dd, H.mosaic);
        }
    }

    // ------------------------------------------------------------------ Moon card

    /** '+9.6' or '−20.1' (U+2212), one decimal. */
    function signedDeg(x) {
        return (x < 0 ? '−' : '+') + Math.abs(x).toFixed(1);
    }

    /** 'Sat 14 Nov' in the page's language. */
    function moonDate(date) {
        const [y, m, d] = date.split('-').map(Number);
        try {
            return new Intl.DateTimeFormat(document.documentElement.lang || 'en',
                {weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC'}).format(new Date(Date.UTC(y, m - 1, d)));
        } catch (e) {
            return europeanDate(date);
        }
    }

    /** The libration line's words for a hint (NovaSolarMoon.hint()), by its display:
     *  a level, 'none', or 'dark' (turned towards us but unlit: never favourable). */
    function librationText(h) {
        const L = (i18n.moon || {}).level || {};
        const deg = h.amount.toFixed(1);
        if (h.display === 'none') return fill(L.none, {deg: deg});
        if (h.display === 'dark') return fill(L.dark, {side: h.side || '', deg: deg});
        return fill(L[h.display], {side: h.side || '', deg: deg})
            + (h.also.length ? ' — ' + h.also.join(', ') : '');
    }

    /** Sunrise or sunset over a feature: a half Sun on the horizon, the arrow up or down. */
    function sideGlyph(parent, morning) {
        const svg = svgEl(parent, 'svg', {viewBox: '0 0 16 16', class: 'solar-moon-glyph', 'aria-hidden': 'true', focusable: 'false'});
        svgEl(svg, 'path', {d: 'M 3 11 A 5 5 0 0 1 13 11 Z'});
        svgEl(svg, 'path', {d: 'M 1 12.5 H 15', class: 'solar-moon-glyph-line'});
        svgEl(svg, 'path', {d: morning ? 'M 8 1 L 5.5 4 H 10.5 Z' : 'M 8 4 L 5.5 1 H 10.5 Z'});
        return svg;
    }

    /** One feature of the list: its number on the drawing, name, sunrise or sunset, and a
     *  bar for the Sun's altitude; the rest in the tooltip. */
    function moonFeatureItem(list, f, n, next) {
        const M = i18n.moon || {};
        const li = document.createElement('li');
        li.className = 'solar-moon-feature';
        li.dataset.side = f.morning ? 'morning' : 'evening';
        li.setAttribute('tabindex', '0');
        if (n) {
            li.dataset.n = String(n);
            span(li, String(n), 'solar-moon-num');
        }
        span(li, f.name, 'solar-moon-name');
        sideGlyph(li, f.morning);
        const bar = span(li, '', 'solar-moon-bar');
        const fillEl = span(bar, '', 'solar-moon-bar-fill');
        fillEl.style.width = Math.round(Math.max(0, Math.min(1, f.sunAlt / MOON_BAR_DEG)) * 100) + '%';
        if (f.nearLimb) li.classList.add('solar-moon-feature-limb');
        const dates = next && next.byName[f.name] ? next.byName[f.name] : [];
        li.title = [
            ((M.types || {})[f.type] || f.type) + ' · ' + Math.round(f.diameterKm) + ' km',
            f.morning ? M.morning : M.evening,
            fill(M.sunAlt, {alt: f.sunAlt.toFixed(1)}),
            fill(M.position, {lat: signedDeg(f.lat), lon: signedDeg(f.lon)}),
            f.nearLimb ? M.nearLimb : '',
            dates.length ? fill(M.nextNights, {dates: dates.map(moonDate).join(', ')}) : M.noNextNights,
        ].filter(Boolean).join('\n');
        li.setAttribute('aria-label', [f.name, (M.types || {})[f.type] || f.type,
            f.morning ? M.morning : M.evening, fill(M.sunAlt, {alt: f.sunAlt.toFixed(1)})].join(', '));
        if (n) {
            li.addEventListener('mouseenter', () => showHotMarker('moon', n));
            li.addEventListener('mouseleave', () => showHotMarker('moon', null));
            li.addEventListener('focus', () => showHotMarker('moon', n));
            li.addEventListener('blur', () => showHotMarker('moon', null));
        }
        list.appendChild(li);
        return li;
    }

    /** A tile: a micro-label, then its value, after what draw(row) puts before it. */
    function cardTile(tiles, prefix, label, value, help, draw) {
        const tile = document.createElement('div');
        tile.className = prefix + '-tile';
        span(tile, label || '', prefix + '-tile-label');
        const row = span(tile, '', prefix + '-tile-row');
        if (draw) draw(row);
        span(row, value, prefix + '-tile-value');
        if (help) tile.title = help;
        tiles.appendChild(tile);
        return tile;
    }

    function moonTile(tiles, label, value, help, draw) {
        return cardTile(tiles, 'solar-moon', label, value, help, draw);
    }

    function renderMoonTiles(t) {
        const M = i18n.moon || {};
        const tiles = $('solar-moon-tiles');
        tiles.innerHTML = '';
        const g = t.geometry, h = t.libration;
        moonTile(tiles, M.colongitude, g.colongitude.toFixed(1) + '°', M.colongitudeHelp);
        // A dial: the hand points to the limb turned towards us (as on the drawing, in its
        // view), longer the more it is turned
        const lib = moonTile(tiles, M.libration, h.amount.toFixed(1) + '°', M.librationHelp + '\n' + librationText(h), row => {
            const dial = svgEl(row, 'svg', {viewBox: '-10 -10 20 20', class: 'solar-moon-dial', 'aria-hidden': 'true', focusable: 'false'});
            svgEl(dial, 'circle', {cx: 0, cy: 0, r: 8.5, class: 'solar-moon-dial-rim'});
            const d = NovaSolarMoon.diagram(g, state.cardView, []).arrow;
            const len = 2 + 6.5 * Math.min(1, h.amount / 8);
            svgEl(dial, 'path', {d: 'M 0 0 L ' + (d.x * len).toFixed(2) + ' ' + (d.y * len).toFixed(2), class: 'solar-moon-dial-hand'});
        });
        lib.dataset.level = h.display;
        moonTile(tiles, M.illuminated, Math.round(t.illuminated * 100) + '%');
    }

    /** The marker of chip n drawn large, over everything else on that card's drawing (its
     *  own top layer), with the chip's number on the Moon's (Mars's chips carry only the
     *  name, so its marker is only ringed); null clears it. Only one shows at a time across
     *  the cards: each chip clears its own card. */
    function showHotMarker(card, n) {
        const hot = card === 'mars' ? state.marsHot : state.moonHot;
        const layer = hot && hot.layer;
        if (!layer) return;
        layer.innerHTML = '';
        const m = n ? hot.markers[n] : null;
        if (!m) return;
        if (card === 'mars') {
            svgEl(layer, 'circle', {cx: m.x, cy: m.y, r: 0.07});
            return;
        }
        svgEl(layer, 'circle', {cx: m.x, cy: m.y, r: 0.085});
        const label = svgEl(layer, 'text', {x: m.x, y: m.y, 'text-anchor': 'middle', 'dominant-baseline': 'central'});
        label.textContent = String(n);
    }

    /** The Moon drawn as tonight, in the chosen view: SVG on a unit disc. */
    function renderMoonDiagram(t) {
        const M = i18n.moon || {};
        const box = $('solar-moon-diagram');
        box.innerHTML = '';
        const d = NovaSolarMoon.diagram(t.geometry, state.cardView, t.top);
        const svg = svgEl(box, 'svg', {viewBox: '-1.3 -1.3 2.6 2.6', role: 'img', class: 'solar-moon-svg',
                                       'aria-label': M.diagramAria || ''});
        svgEl(svg, 'circle', {cx: 0, cy: 0, r: 1, class: 'solar-moon-dark'});
        // The lit side over a surface-coloured copy of itself, so a lighter tint of the
        // Moon's colour needs no new colour (solar_system.css)
        if (d.lit) {
            svgEl(svg, 'path', {d: d.lit, class: 'solar-moon-lit-base'});
            svgEl(svg, 'path', {d: d.lit, class: 'solar-moon-lit'});
        }
        if (d.terminator) svgEl(svg, 'path', {d: d.terminator, class: 'solar-moon-terminator'});
        svgEl(svg, 'circle', {cx: 0, cy: 0, r: 1, class: 'solar-moon-rim'});

        // The libration arrow just outside the limb, pointing in
        const a = d.arrow, px = -a.y, py = a.x;
        const tip = [a.x * 1.04, a.y * 1.04], base = [a.x * 1.24, a.y * 1.24];
        const arrow = svgEl(svg, 'path', {
            d: 'M ' + tip.map(v => v.toFixed(3)).join(' ')
                + ' L ' + (base[0] + px * 0.1).toFixed(3) + ' ' + (base[1] + py * 0.1).toFixed(3)
                + ' L ' + (base[0] - px * 0.1).toFixed(3) + ' ' + (base[1] - py * 0.1).toFixed(3) + ' Z',
            class: 'solar-moon-arrow', 'data-level': t.libration.display,
        });
        svgEl(arrow, 'title', {}).textContent = librationText(t.libration);

        const north = svgEl(svg, 'text', {x: (d.north.x * 1.16).toFixed(3), y: (d.north.y * 1.16).toFixed(3),
                                          class: 'solar-moon-north', 'text-anchor': 'middle', 'dominant-baseline': 'central'});
        north.textContent = M.north || 'N';

        // A small dot per feature, no number: the number shows only for the chip under the
        // pointer or focus (showHotMarker), so labels never overlap
        const markers = {};
        d.markers.forEach(m => {
            const g = svgEl(svg, 'g', {class: 'solar-moon-marker', 'data-n': m.n});
            svgEl(g, 'circle', {cx: m.x, cy: m.y, r: 0.034});
            svgEl(g, 'title', {}).textContent = m.name;
            markers[m.n] = m;
        });
        state.moonHot = { layer: svgEl(svg, 'g', {class: 'solar-moon-hot', 'aria-hidden': 'true'}), markers: markers };
    }

    /** Every card's view buttons: the Moon's own, and the two cards' shared group. */
    function viewButtons() {
        return Array.prototype.slice.call(document.querySelectorAll(
            '.solar-moon-views .solar-moon-view, .solar-card-views .solar-card-view'));
    }

    const buttonView = btn => btn.dataset.moonView || btn.dataset.cardView || '';

    function syncViewButtons() {
        viewButtons().forEach(btn => {
            btn.setAttribute('aria-pressed', buttonView(btn) === state.cardView ? 'true' : 'false');
        });
    }

    /** The views the loaded cards offer: the same three names in every card. */
    function cardViews() {
        if (typeof NovaSolarMoon !== 'undefined') return NovaSolarMoon.VIEWS;
        if (typeof NovaSolarJupiter !== 'undefined') return NovaSolarJupiter.VIEWS;
        if (typeof NovaSolarMars !== 'undefined') return NovaSolarMars.VIEWS;
        return null;
    }

    /** The view switch, from and to localStorage; a change only redraws. All three cards
     *  share one choice, and the Moon's old key is adopted the first time after the
     *  change (its value was the whole of it then). */
    function initCardViews() {
        const views = cardViews();
        if (!views) return;
        let saved = storageGet('localStorage', VIEW_KEY);
        if (views.indexOf(saved) < 0 && views.indexOf(storageGet('localStorage', MOON_VIEW_KEY)) >= 0) {
            saved = storageGet('localStorage', MOON_VIEW_KEY);
            storageSet('localStorage', VIEW_KEY, saved);
        }
        state.cardView = views.indexOf(saved) >= 0 ? saved : 'normal';
        syncViewButtons();
        viewButtons().forEach(btn => {
            btn.addEventListener('click', () => {
                const view = buttonView(btn);
                if (views.indexOf(view) < 0) return;
                state.cardView = view;
                storageSet('localStorage', VIEW_KEY, view);
                syncViewButtons();
                renderMoon();
                renderJupiter();
                renderMars();
            });
        });
    }

    function showMoonError(message) {
        const el = $('solar-moon-error');
        el.textContent = message || '';
        el.hidden = !message;
        $('solar-moon-body').hidden = !!message;
    }

    /** A card's own error line, or null to clear it: while it shows, that card's body is
     *  hidden. The Moon's card has its own (it came first); the two new cards share this. */
    function showCardError(card, message) {
        const el = $('solar-' + card + '-error');
        el.textContent = message || '';
        el.hidden = !message;
        $('solar-' + card + '-body').hidden = !!message;
    }

    /** The Moon card, while the Moon is selected; computed once per night (see the header). */
    function renderMoon() {
        const box = $('solar-moon');
        if (typeof NovaSolarMoon === 'undefined' || !state.result || state.selected !== 'moon') {
            box.hidden = true;
            return;
        }
        box.hidden = false;
        const M = i18n.moon || {};
        let t, next, drawn;
        try {
            const key = calendarKey();
            drawn = JSON.stringify([key, simDate(), state.cardView, document.documentElement.lang || '']);
            if (state.moonDrawn === drawn) return;
            if (state.moon.key !== key) {
                const opts = {inputs: state.inputs, nowMs: Date.now(), simDate: simDate()};
                state.moon = { key: null, tonight: NovaSolarMoon.tonight(opts), next: NovaSolarMoon.nextNights(opts) };
                state.moon.key = key;
            }
            t = state.moon.tonight;
            next = state.moon.next;
            renderMoonDiagram(t);
            renderMoonTiles(t);
        } catch (e) {
            console.error('Solar System: could not calculate the Moon card', e);
            state.moon = { key: null, tonight: null, next: null };
            state.moonDrawn = null;
            $('solar-moon-title').textContent = M.title || bodyName('moon');
            showMoonError(M.error);
            return;
        }
        showMoonError(null);
        $('solar-moon-title').textContent = (M.title || bodyName('moon')) + ' · ' + fill(M.highestAt, {time: t.hm});

        const body = $('solar-moon-body');
        body.classList.toggle('solar-moon-muted', t.muted);
        const empty = $('solar-moon-empty');
        const reason = t.muted ? (t.mutedReason === 'notUp' ? (M.empty || {}).notUp : i18n.belowThreshold)
            : t.empty ? (M.empty || {})[t.empty] : '';
        empty.textContent = reason || '';
        empty.hidden = !reason;

        const list = $('solar-moon-features'), more = $('solar-moon-more'), moreList = $('solar-moon-more-list');
        list.innerHTML = '';
        moreList.innerHTML = '';
        t.top.forEach((f, i) => moonFeatureItem(list, f, i + 1, next));
        t.more.forEach(f => moonFeatureItem(moreList, f, 0, next));
        more.hidden = !t.more.length;

        const lib = $('solar-moon-libration');
        lib.textContent = librationText(t.libration);
        lib.dataset.level = t.libration.display;

        const source = typeof NovaMoonFeatures !== 'undefined' ? NovaMoonFeatures.SOURCE : null;
        const credit = $('solar-moon-credit');
        credit.textContent = source ? fill(M.credit, {source: source.credit}) : '';
        credit.hidden = !source;
        state.moonDrawn = drawn;
    }

    // ------------------------------------------------- Jupiter and Mars cards

    /** One row of one of the cards' lists, in the Moon card's chip shape. */
    function cardItem(list, className) {
        const li = document.createElement('li');
        li.className = 'solar-card-item' + (className ? ' ' + className : '');
        list.appendChild(li);
        return li;
    }

    /** An instant as the location's own local date and time: 'Wed 4 Nov 02:27' in the
     *  page's language (European order for English, as the rest of Nova), so an event after
     *  midnight carries its own date instead of a '+1'. */
    function localDateTime(utcMs) {
        const tz = String(state.inputs.location.timezone);
        const p = NovaAstro.localParts(utcMs, tz);
        const lang = document.documentElement.lang || 'en';
        let date;
        try {
            date = new Intl.DateTimeFormat(lang === 'en' ? 'en-GB' : lang,
                {weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC'})
                .format(new Date(Date.UTC(p.year, p.month - 1, p.day)));
        } catch (e) {
            date = europeanDate(p.year + '-' + String(p.month).padStart(2, '0') + '-' + String(p.day).padStart(2, '0'));
        }
        return date + ' ' + NovaAstro.formatHM(utcMs, tz);
    }

    /** Where one Galilean moon is tonight: which side of Jupiter, in front of the disc or
     *  behind the planet, and whether Jupiter's shadow has it. */
    function moonPlace(m, J) {
        return [m.x >= 0 ? J.east : J.west, m.onDisc ? J.onDisc : m.front ? J.inFront : J.behind,
                m.eclipsed ? J.eclipse : ''].filter(Boolean).join(' · ');
    }

    /** One of the night's phenomena: its date and time, the moon and what it is doing, how
     *  long it lasts, and whether it is a double shadow transit. Below the threshold: muted. */
    function jupiterEventItem(list, e) {
        const J = i18n.jupiter || {};
        const li = cardItem(list, 'solar-card-event');
        li.dataset.type = e.type;
        if (!e.visible) li.classList.add('solar-card-below');
        span(li, localDateTime(e.startUtcMs), 'solar-card-time');
        span(li, (J.eventTypes || {})[e.type] || e.type, 'solar-card-name');
        span(li, (J.moonNames || {})[e.moon] || e.moon, 'solar-card-note');
        span(li, fill(J.minutes, {n: e.minutes}), 'solar-card-note');
        if (e.double) span(li, J.double, 'solar-card-badge');
        li.title = [(J.eventTypes || {})[e.type], fill(J.minutes, {n: e.minutes}),
                    fill(J.altitude, {alt: wholeDeg(e.alt)}),
                    e.double ? J.doubleHelp : '',
                    e.visible ? '' : i18n.belowThreshold].filter(Boolean).join('\n');
        return li;
    }

    /** One of the next nights' shadow transits: its date and time, the moons, the length. */
    function jupiterShadowItem(list, s) {
        const J = i18n.jupiter || {};
        const li = cardItem(list, 'solar-card-event');
        li.dataset.type = 'shadow';
        const when = localDateTime(s.utcMs);
        span(li, when, 'solar-card-time');
        span(li, s.moons.map(m => (J.moonNames || {})[m] || m).join(' + '), 'solar-card-name');
        span(li, fill(J.minutes, {n: s.minutes}), 'solar-card-note');
        if (s.double) span(li, J.double, 'solar-card-badge');
        li.title = [fill(J.shadowAt, {time: when}), s.double ? J.doubleHelp : ''].filter(Boolean).join('\n');
        return li;
    }

    function jupiterTiles(t) {
        const J = i18n.jupiter || {};
        const tiles = $('solar-jupiter-tiles');
        tiles.innerHTML = '';
        const cm = t.geometry.cm;
        cardTile(tiles, 'solar-card', J.cmII, cm.II.toFixed(0) + '°', [J.cmIIHelp,
            fill(J.systemI, {deg: cm.I.toFixed(0)}), fill(J.systemIII, {deg: cm.III.toFixed(0)})].join('\n'));
    }

    // --- the Great Red Spot

    const GRS_KEY = 'nova_solar_grs';

    function storageRemove(store, key) {
        try { window[store].removeItem(key); } catch (e) { /* private mode: nothing kept */ }
    }

    /** The user's own GRS longitude, from localStorage: { lon, date } when it is usable,
     *  else null (none, unreadable, or storage blocked). */
    function loadGrsOverride() {
        let o = null;
        try { o = JSON.parse(storageGet('localStorage', GRS_KEY) || 'null'); } catch (e) { o = null; }
        return o && NovaSolarJupiter.grsReference(o).user ? {lon: o.lon, date: o.date} : null;
    }

    /** The GRS tile: tonight's predicted transits large, the longitude the prediction starts
     *  from and its date under them (the warning colour once that is over six months old),
     *  the next nights' transits in the tooltip; Edit and Reset inline. */
    function renderGrs(grs) {
        const J = i18n.jupiter || {};
        const G = J.grs || {};
        const tile = $('solar-jupiter-grs');
        const times = $('solar-jupiter-grs-times');
        times.textContent = grs.tonight.length ? grs.tonight.map(x => x.hm).join(' · ') : G.none || '';
        times.classList.toggle('solar-card-grs-none', !grs.tonight.length);
        $('solar-jupiter-grs-meta').textContent = fill(G.meta, {lon: grs.refLon.toFixed(1), date: europeanDate(grs.refDate)});
        tile.dataset.stale = grs.stale ? 'true' : 'false';
        $('solar-jupiter-grs-reset').hidden = !grs.user;
        tile.title = [
            G.help,
            grs.tonight.length ? fill(G.tonight, {times: grs.tonight.map(x => localDateTime(x.utcMs)).join(', ')}) : '',
            grs.next.length ? G.next + '\n' + grs.next.map(x => localDateTime(x.utcMs)).join('\n') : G.noNext,
            fill(grs.user ? G.user : G.source, {lon: grs.refLon.toFixed(1), date: europeanDate(grs.refDate)}),
            fill(G.drift, {rate: '+' + grs.driftPerMonth.toFixed(2)}),
            grs.stale ? G.stale : '',
        ].filter(Boolean).join('\n');
    }

    function grsForm(open) {
        $('solar-jupiter-grs-form').hidden = !open;
        $('solar-jupiter-grs-edit').hidden = open;
        $('solar-jupiter-grs-invalid').hidden = true;
    }

    /** Edit, Save, Cancel and Reset. A value that cannot be kept (storage blocked) still
     *  holds for this visit. */
    function initGrs() {
        if (typeof NovaSolarJupiter === 'undefined') return;
        state.grsOverride = loadGrsOverride();
        grsForm(false);
        const redraw = () => { state.jupiterDrawn = null; renderJupiter(); };
        $('solar-jupiter-grs-edit').addEventListener('click', () => {
            const ref = NovaSolarJupiter.grsReference(state.grsOverride);
            $('solar-jupiter-grs-lon').value = ref.lon.toFixed(1);
            $('solar-jupiter-grs-date').value = ref.date;
            grsForm(true);
        });
        $('solar-jupiter-grs-cancel').addEventListener('click', () => grsForm(false));
        $('solar-jupiter-grs-save').addEventListener('click', () => {
            const raw = String($('solar-jupiter-grs-lon').value).trim().replace(',', '.');
            const o = {lon: raw === '' ? NaN : Math.round(Number(raw) * 10) / 10,
                       date: String($('solar-jupiter-grs-date').value)};
            if (!NovaSolarJupiter.grsReference(o).user) {
                $('solar-jupiter-grs-invalid').hidden = false;
                return;
            }
            state.grsOverride = o;
            storageSet('localStorage', GRS_KEY, JSON.stringify(o));
            grsForm(false);
            redraw();
        });
        $('solar-jupiter-grs-reset').addEventListener('click', () => {
            state.grsOverride = null;
            storageRemove('localStorage', GRS_KEY);
            grsForm(false);
            redraw();
        });
    }

    // --- the drawing

    // The strip's height, and the moon names' size and lanes, in pixels: the figure is drawn
    // at the width it is shown, so the scale is fixed by the strip and the text is not
    // scaled with it.
    const JUP_HEIGHT = 160;
    const JUP_LABEL_PX = 12;
    const JUP_CHAR_PX = 7;
    const JUP_LANES = [-15, 17, -31, 33, -47, 49];

    /** A label's box, from its centre and text. */
    function labelBox(x, y, text) {
        const w = text.length * JUP_CHAR_PX + 4;
        return {x0: x - w / 2, x1: x + w / 2, y0: y - JUP_LABEL_PX / 2 - 1, y1: y + JUP_LABEL_PX / 2 + 1};
    }

    const overlaps = (a, b) => a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;

    /** Jupiter as a system at one fixed scale: the disc in the centre, STRIP_RJ radii each
     *  side, one symbol per moon by what it is doing, the shadows on the disc, the Great
     *  Red Spot when it is on the side facing us, and where celestial north lies. The names
     *  are set at a fixed size and staggered above and below so no two overlap. */
    function jupiterDiagram(t, grsLon) {
        const J = i18n.jupiter || {};
        const box = $('solar-jupiter-diagram');
        box.innerHTML = '';
        const W = Math.max(240, Math.round(box.clientWidth || 0) || 720);
        const H = JUP_HEIGHT, cx = W / 2, cy = H / 2;
        const k = (cx - 8) / NovaSolarJupiter.STRIP_RJ;        // pixels per Jupiter radius
        const px = v => Math.round(v * 100) / 100;
        const d = NovaSolarJupiter.diagram(t.geometry, state.cardView, t.moons, grsLon);
        const svg = svgEl(box, 'svg', {
            width: W, height: H, viewBox: '0 0 ' + W + ' ' + H,
            role: 'img', class: 'solar-card-svg solar-jupiter-svg', 'aria-label': J.diagramAria || '',
            'data-scale': Math.round(k * 1e6) / 1e6,
        });
        svgEl(svg, 'title', {}).textContent = [J.diagramAria, J.legendVisible, J.legendBehind,
            J.legendEclipsed, J.legendShadow, J.legendGrs].filter(Boolean).join('\n');
        svgEl(svg, 'path', {d: 'M 4 ' + cy + ' H ' + (W - 4), class: 'solar-card-equator'});
        svgEl(svg, 'circle', {cx: px(cx), cy: cy, r: px(k), class: 'solar-card-disc'});
        if (d.grs && d.grs.visible) {
            const g = svgEl(svg, 'g', {class: 'solar-card-grs'});
            const rx = Math.max(3, 0.3 * k);
            svgEl(g, 'ellipse', {cx: px(cx + d.grs.x * k), cy: px(cy + d.grs.y * k), rx: px(rx), ry: px(rx * 0.6)});
            svgEl(g, 'title', {}).textContent = J.grsMarker || '';
        }
        d.moons.forEach(m => {
            if (!m.shadow) return;
            const g = svgEl(svg, 'g', {class: 'solar-card-shadow', 'data-moon': m.name});
            svgEl(g, 'circle', {cx: px(cx + m.shadow.x * k), cy: px(cy + m.shadow.y * k), r: 2.5});
            svgEl(g, 'title', {}).textContent = ((J.moonNames || {})[m.name] || m.name) + ' — ' + J.shadowOnDisc;
        });

        // North first, so the names keep clear of it
        const nx = cx + d.north.x * (k + 14), ny = cy + d.north.y * (k + 14);
        const placed = [labelBox(nx, ny, 'N'), {x0: cx - k - 2, x1: cx + k + 2, y0: cy - k - 2, y1: cy + k + 2}];
        const north = svgEl(svg, 'text', {x: px(nx), y: px(ny), class: 'solar-card-north',
                                          'text-anchor': 'middle', 'dominant-baseline': 'central'});
        north.textContent = J.north || 'N';
        svgEl(north, 'title', {}).textContent = J.northHelp || '';

        // The moons, then their names, the nearest the disc placed first
        // The tooltip's east and west are the sky's (t.moons, x east), not the drawing's
        const drawn = d.moons.map((m, i) => {
            const name = (J.moonNames || {})[m.name] || m.name;
            const sky = t.moons[i];
            const x = cx + m.x * k, y = cy + m.y * k;
            const g = svgEl(svg, 'g', {class: 'solar-card-moon solar-card-moon-' + m.state, 'data-moon': m.name});
            svgEl(g, 'circle', {cx: px(x), cy: px(y), r: 4});
            svgEl(g, 'title', {}).textContent = [name, moonPlace(sky, J),
                fill(J.rjFromCentre, {rj: Math.hypot(sky.x, sky.y).toFixed(1)})].join('\n');
            return {m: m, name: name, x: x, y: y};
        });
        drawn.slice().sort((a, b) => Math.abs(a.m.x) - Math.abs(b.m.x)).forEach(o => {
            const half = (o.name.length * JUP_CHAR_PX + 4) / 2;
            const lx = Math.min(W - half, Math.max(half, o.x));
            let spot = null;
            for (const dy of JUP_LANES) {
                const y = Math.min(H - JUP_LABEL_PX / 2, Math.max(JUP_LABEL_PX / 2, o.y + dy));
                const b = labelBox(lx, y, o.name);
                if (!placed.some(p => overlaps(p, b))) { spot = {y: y, b: b}; break; }
            }
            if (!spot) {
                const y = Math.min(H - JUP_LABEL_PX / 2, Math.max(JUP_LABEL_PX / 2, o.y + JUP_LANES[0]));
                spot = {y: y, b: labelBox(lx, y, o.name)};
            }
            placed.push(spot.b);
            const label = svgEl(svg, 'text', {x: px(lx), y: px(spot.y), class: 'solar-card-moon-label',
                                              'data-moon': o.m.name, 'text-anchor': 'middle', 'dominant-baseline': 'central'});
            label.textContent = o.name;
        });
        state.jupiterWidth = W;
    }

    /** The Jupiter card, while Jupiter is selected; computed once per night, as the Moon's,
     *  and the GRS again when its longitude is edited. */
    function renderJupiter() {
        const box = $('solar-jupiter');
        if (typeof NovaSolarJupiter === 'undefined' || !state.result || state.selected !== 'jupiter') {
            box.hidden = true;
            return;
        }
        box.hidden = false;
        const J = i18n.jupiter || {};
        let t, next, grs, drawn;
        try {
            const key = calendarKey();
            const grsKey = JSON.stringify(state.grsOverride);
            const width = Math.round($('solar-jupiter-diagram').clientWidth || 0);
            drawn = JSON.stringify([key, simDate(), state.cardView, document.documentElement.lang || '', grsKey, width]);
            if (state.jupiterDrawn === drawn) return;
            const opts = {inputs: state.inputs, nowMs: Date.now(), simDate: simDate()};
            if (state.jupiter.key !== key) {
                state.jupiter = {key: null, tonight: NovaSolarJupiter.tonight(opts),
                                 next: NovaSolarJupiter.nextNights(opts), grs: null, grsKey: null};
                state.jupiter.key = key;
            }
            if (state.jupiter.grsKey !== grsKey) {
                state.jupiter.grs = NovaSolarJupiter.grs(opts, state.grsOverride);
                state.jupiter.grsKey = grsKey;
            }
            t = state.jupiter.tonight;
            next = state.jupiter.next;
            grs = state.jupiter.grs;
            const ref = NovaSolarJupiter.grsReference(state.grsOverride);
            jupiterDiagram(t, NovaSolarJupiter.grsLongitude(ref, t.instantUtc));
            jupiterTiles(t);
            renderGrs(grs);
        } catch (e) {
            console.error('Solar System: could not calculate the Jupiter card', e);
            state.jupiter = {key: null, tonight: null, next: null, grs: null, grsKey: null};
            state.jupiterDrawn = null;
            $('solar-jupiter-title').textContent = J.title || bodyName('jupiter');
            showCardError('jupiter', J.error);
            return;
        }
        showCardError('jupiter', null);
        $('solar-jupiter-title').textContent = (J.title || bodyName('jupiter')) + ' · '
            + fill(J.highestAt, {time: t.hm});

        $('solar-jupiter-body').classList.toggle('solar-card-muted', t.muted);

        const empty = $('solar-jupiter-empty');
        const reason = t.muted ? (t.mutedReason === 'notUp' ? (J.empty || {}).notUp : i18n.belowThreshold)
            : t.events.length ? '' : J.noEvents;
        empty.textContent = reason || '';
        empty.hidden = !reason;

        const events = $('solar-jupiter-events');
        events.innerHTML = '';
        t.events.forEach(e => jupiterEventItem(events, e));

        const shadows = $('solar-jupiter-shadows');
        shadows.innerHTML = '';
        next.shadows.forEach(s => jupiterShadowItem(shadows, s));
        const none = $('solar-jupiter-no-shadows');
        none.textContent = next.shadows.length ? '' : J.noShadows;
        none.hidden = !!next.shadows.length;
        state.jupiterDrawn = drawn;
    }

    /** The strip is drawn at the width it is shown: a new width draws it again. */
    function initJupiterResize() {
        if (typeof ResizeObserver === 'undefined') return;
        const box = $('solar-jupiter-diagram');
        new ResizeObserver(() => {
            const W = Math.round(box.clientWidth || 0);
            if (!W || W === state.jupiterWidth || $('solar-jupiter').hidden) return;
            renderJupiter();
        }).observe(box);
    }

    // ------------------------------------------------------ Mars card

    /** One named feature: its name, and a bar for how close it stands to the central
     *  meridian; its type, place, what the bar means and its next passage in the tooltip. */
    function marsFeatureItem(list, f, n) {
        const M = i18n.mars || {};
        const li = cardItem(list, 'solar-card-feature');
        li.dataset.side = f.cmDist >= 0 ? 'east' : 'west';
        li.setAttribute('tabindex', '0');
        if (n) li.dataset.n = String(n);
        span(li, f.name, 'solar-card-name');
        const bar = span(li, '', 'solar-card-bar');
        span(bar, '', 'solar-card-bar-fill').style.width =
            Math.round(Math.max(0, 1 - Math.abs(f.cmDist) / NovaSolarMars.NEAR_LIMB_DEG) * 100) + '%';
        if (f.nearLimb) li.classList.add('solar-card-feature-limb');
        const type = (M.types || {})[f.type] || f.type;
        const deg = Math.abs(Math.round(f.cmDist));
        li.title = [
            type + (f.diameterKm ? ' · ' + Math.round(f.diameterKm) + ' km' : ''),
            fill(M.position, {lat: signedDeg(f.lat), lon: signedDeg(f.lon)}),
            fill(M.bar, {deg: deg}),
            f.nearLimb ? M.nearLimb : '',
            f.nextUtcMs === null ? '' : fill(M.nextOnCm, {time: localDateTime(f.nextUtcMs)}),
        ].filter(Boolean).join('\n');
        li.setAttribute('aria-label', [f.name, type, fill(M.cmDist, {deg: deg})].join(', '));
        if (n) {
            li.addEventListener('mouseenter', () => showHotMarker('mars', n));
            li.addEventListener('mouseleave', () => showHotMarker('mars', null));
            li.addEventListener('focus', () => showHotMarker('mars', n));
            li.addEventListener('blur', () => showHotMarker('mars', null));
        }
        return li;                                  // cardItem has already appended it
    }

    /** A pole's tilt in words: 'North pole tilted 20° towards us'. */
    function poleWords(deg, north, south) {
        return fill(deg >= 0 ? north : south, {deg: Math.abs(Math.round(deg))});
    }

    function marsTiles(t) {
        const M = i18n.mars || {};
        const tiles = $('solar-mars-tiles');
        tiles.innerHTML = '';
        const g = t.geometry;
        cardTile(tiles, 'solar-card', M.cm, g.cm.toFixed(0) + '°', [M.cmHelp,
            poleWords(g.de, M.deNorth, M.deSouth), poleWords(g.ds, M.dsNorth, M.dsSouth)].join('\n'));
        const best = t.best;
        const tile = cardTile(tiles, 'solar-card', M.best, best ? best.hm : '–',
            best ? fill(M.bestHelp, {name: best.name}) : i18n.belowThreshold);
        if (best) span(tile, best.name, 'solar-card-tile-note');
    }

    /** Mars's disc as the sky has it tonight (celestial north up): the polar cap tilted
     *  towards us, the unlit part, the terminator, a dot per named feature, and the pole it
     *  leans by, its N in HTML over the drawing at a fixed size. */
    function marsDiagram(t) {
        const M = i18n.mars || {};
        const box = $('solar-mars-diagram');
        box.innerHTML = '';
        const d = NovaSolarMars.diagram(t.geometry, state.cardView, t.top);
        const svg = svgEl(box, 'svg', {
            viewBox: '-1.3 -1.3 2.6 2.6', role: 'img', class: 'solar-card-svg solar-mars-svg',
            'aria-label': M.diagramAria || '',
        });
        svgEl(svg, 'circle', {cx: 0, cy: 0, r: 1, class: 'solar-card-surface'});
        d.caps.forEach(c => {
            const g = svgEl(svg, 'g', {class: 'solar-card-cap', 'data-pole': c.pole});
            svgEl(g, 'path', {d: c.d});
            svgEl(g, 'title', {}).textContent = c.pole === 'north' ? M.capNorth : M.capSouth;
        });
        if (d.shaded) svgEl(svg, 'path', {d: d.shaded, class: 'solar-card-shaded'});
        if (d.terminator) svgEl(svg, 'path', {d: d.terminator, class: 'solar-card-terminator'});
        svgEl(svg, 'circle', {cx: 0, cy: 0, r: 1, class: 'solar-card-rim'});

        // The pole: a tick inside the limb
        const pole = svgEl(svg, 'g', {class: 'solar-card-pole'});
        svgEl(pole, 'path', {d: 'M ' + (d.pole.x * 0.74).toFixed(3) + ' ' + (d.pole.y * 0.74).toFixed(3)
            + ' L ' + d.pole.x.toFixed(3) + ' ' + d.pole.y.toFixed(3)});
        svgEl(pole, 'title', {}).textContent = poleWords(d.tilt, M.deNorth, M.deSouth);

        // A small dot per feature, no number: the chip under the pointer or focus is
        // ringed on the drawing (showHotMarker)
        const markers = {};
        d.markers.forEach(m => {
            const g = svgEl(svg, 'g', {class: 'solar-card-marker', 'data-n': m.n});
            svgEl(g, 'circle', {cx: m.x, cy: m.y, r: 0.034});
            svgEl(g, 'title', {}).textContent = m.name;
            markers[m.n] = m;
        });
        state.marsHot = {layer: svgEl(svg, 'g', {class: 'solar-card-hot', 'aria-hidden': 'true'}), markers: markers};

        // N beside the pole, in HTML: a fixed 12 px at any drawing size
        const north = span(box, M.north || 'N', 'solar-card-north-label');
        north.style.left = ((d.pole.x * 1.16 + 1.3) / 2.6 * 100).toFixed(2) + '%';
        north.style.top = ((d.pole.y * 1.16 + 1.3) / 2.6 * 100).toFixed(2) + '%';
        north.title = poleWords(d.tilt, M.deNorth, M.deSouth);
    }

    /** The Mars card, while Mars is selected; computed once per night, as the Moon's. */
    function renderMars() {
        const box = $('solar-mars');
        if (typeof NovaSolarMars === 'undefined' || !state.result || state.selected !== 'mars') {
            box.hidden = true;
            return;
        }
        box.hidden = false;
        const M = i18n.mars || {};
        let t, drawn;
        try {
            const key = calendarKey();
            drawn = JSON.stringify([key, simDate(), state.cardView, document.documentElement.lang || '']);
            if (state.marsDrawn === drawn) return;
            if (state.mars.key !== key) {
                const opts = {inputs: state.inputs, nowMs: Date.now(), simDate: simDate()};
                state.mars = {key: null, tonight: NovaSolarMars.tonight(opts)};
                state.mars.key = key;
            }
            t = state.mars.tonight;
            marsDiagram(t);
            marsTiles(t);
        } catch (e) {
            console.error('Solar System: could not calculate the Mars card', e);
            state.mars = {key: null, tonight: null};
            state.marsDrawn = null;
            $('solar-mars-title').textContent = M.title || bodyName('mars');
            showCardError('mars', M.error);
            return;
        }
        showCardError('mars', null);
        $('solar-mars-title').textContent = (M.title || bodyName('mars')) + ' · '
            + fill(M.highestAt, {time: t.hm});

        $('solar-mars-body').classList.toggle('solar-card-muted', t.muted);

        const empty = $('solar-mars-empty');
        const reason = t.muted ? (t.mutedReason === 'notUp' ? (M.empty || {}).notUp : i18n.belowThreshold)
            : t.empty ? (M.empty || {})[t.empty] : '';
        empty.textContent = reason || '';
        empty.hidden = !reason;

        const list = $('solar-mars-features'), more = $('solar-mars-more'), moreList = $('solar-mars-more-list');
        list.innerHTML = '';
        moreList.innerHTML = '';
        t.top.forEach((f, i) => marsFeatureItem(list, f, i + 1));
        t.more.forEach(f => marsFeatureItem(moreList, f, 0));
        more.hidden = !t.more.length;

        const source = typeof NovaMarsFeatures !== 'undefined' ? NovaMarsFeatures.SOURCE : null;
        const credit = $('solar-mars-credit');
        credit.textContent = source ? fill(M.credit, {source: source.credit}) : '';
        credit.hidden = !source;
        state.marsDrawn = drawn;
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
            renderMoon();
            renderJupiter();
            renderMars();
            renderCalc();
            showChart(force);
            if (state.chart) state.chart.updateNow();
            loadWind();
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

    /** The filter: a body (a conjunction matches either of its two), an event type and
     *  "hide muted". */
    function calRowShown(row) {
        if (state.calFilter.hideMuted && row.muted) return false;
        if (state.calFilter.type && CAL_TYPES[state.calFilter.type].indexOf(row.kind) < 0) return false;
        return !state.calFilter.body || row.bodies.indexOf(state.calFilter.body) >= 0;
    }

    /** The local calendar day number of an instant at the location, for the day suffixes
     *  the calendar's time cells carry ("HH:MM +1"). */
    function localDay(tz, utcMs) {
        const p = NovaAstro.localParts(utcMs, tz);
        return Date.UTC(p.year, p.month - 1, p.day) / 86400000;
    }

    /** Why a Moon conjunction is muted: daylight, or the two altitudes at its instant. */
    function momentText(row) {
        if (row.moment.daylight) return i18n.daylightAtClosest;
        return fill(i18n.belowThresholdAtClosest, {
            first: bodyName(row.bodies[0]), first_alt: wholeDeg(row.moment.alts[0]),
            second: bodyName(row.bodies[1]), second_alt: wholeDeg(row.moment.alts[1]),
        });
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
                tr.title = row.mutedReason === 'moment' ? momentText(row)
                    : row.mutedReason === 'sun' ? fill(i18n.tooCloseToSun, {elong: row.elongationDeg.toFixed(0)})
                    : row.mutedReason === 'lunarWindow' ? i18n.lunarNotVisible
                    : i18n.belowThresholdNight;
            }
            // The night's date only; the event's own local time is the cell's tooltip
            const date = cell(tr, 'date', europeanDate(row.night));
            date.title = String(i18n.exactTime).replace('{time}', NovaSolarRows.timeLabel(row.hm, row.hmDay));
            if (row.kind === 'galilean') {
                // A double shadow transit runs for a while, so the end goes in the tooltip
                // too, with the same day suffix the start's cell tooltip carries.
                const tz = NovaSolarRows._night.site(state.inputs).tz;
                const night = localDay(tz, row.utcMs) - row.hmDay;
                const end = NovaSolarRows.timeLabel(NovaAstro.formatHM(row.endUtcMs, tz),
                                                    localDay(tz, row.endUtcMs) - night);
                date.title += '\n' + fill((i18n.jupiter || {}).until, {time: end});
            }
            cell(tr, 'body', row.bodies.map(bodyName).join(' – '));
            const moons = row.kind === 'galilean'
                ? row.moons.map(m => ((i18n.jupiter || {}).moonNames || {})[m] || m).join(' + ') : '';
            const event = cell(tr, 'event', row.occultation ? i18n.occultation
                : conj ? fill(i18n.conjunction, {sep: row.sepDeg.toFixed(1)})
                : row.kind === 'libration' ? librationText(row.libration)
                : row.kind === 'galilean' ? fill(i18n.galilean, {moons: moons, minutes: row.minutes})
                : (i18n[row.kind] || row.kind));
            const helpText = row.occultation ? help.occultation
                : conj ? help[row.bodies.indexOf('moon') >= 0 ? 'conjunctionMoon' : 'conjunction'] : help[row.kind];
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
            const opts = {inputs: state.inputs, nowMs: Date.now(), simDate: simDate()};
            const result = NovaSolarCalendar.events(opts);
            // The lunar rows on their own: if they fail, the planets' rows still show
            if (typeof NovaSolarMoon !== 'undefined') {
                try {
                    result.rows = result.rows.concat(NovaSolarMoon.calendarRows(opts).rows)
                        .sort((a, b) => a.utcMs - b.utcMs);
                } catch (e) {
                    console.error('Solar System: could not calculate the lunar Calendar events', e);
                }
            }
            // And the Galilean ones the same way: two moons' shadows on Jupiter at once,
            // while it is above the threshold at night. The module scans those nights
            // itself (see its header) and memoises on this same key, so this is once a night.
            if (typeof NovaSolarJupiter !== 'undefined') {
                try {
                    result.rows = result.rows.concat(NovaSolarJupiter.calendarRows(opts).rows)
                        .sort((a, b) => a.utcMs - b.utcMs);
                } catch (e) {
                    console.error('Solar System: could not calculate the Galilean Calendar events', e);
                }
            }
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
        const type = $('solar-cal-type-filter');
        const knownType = v => (Object.prototype.hasOwnProperty.call(CAL_TYPES, v) ? v : '');
        const saved = storageGet('localStorage', CAL_BODY_KEY);
        state.calFilter.body = NovaPlanets.BODIES.indexOf(saved) >= 0 ? saved : '';
        state.calFilter.type = knownType(storageGet('localStorage', CAL_TYPE_KEY));
        state.calFilter.hideMuted = storageGet('localStorage', CAL_HIDE_KEY) === 'true';
        select.value = state.calFilter.body;
        type.value = state.calFilter.type;
        hide.checked = state.calFilter.hideMuted;
        const apply = () => { if (state.calResult) renderCalendarRows(state.calResult); };
        select.addEventListener('change', () => {
            state.calFilter.body = NovaPlanets.BODIES.indexOf(select.value) >= 0 ? select.value : '';
            storageSet('localStorage', CAL_BODY_KEY, state.calFilter.body);
            apply();
        });
        type.addEventListener('change', () => {
            state.calFilter.type = knownType(type.value);
            storageSet('localStorage', CAL_TYPE_KEY, state.calFilter.type);
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
        initCardViews();
        initGrs();
        initJupiterResize();
        initCalc();
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
