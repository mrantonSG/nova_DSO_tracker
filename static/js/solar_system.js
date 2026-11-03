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
 * faces us, and an arrow to celestial north; its names are set at a fixed size and
 * staggered so they never overlap, and the strip is redrawn when its width changes --
 * with a tile for the Great Red Spot's predicted transits (the longitude editable, kept
 * under nova_solar_grs) and one for the System II central meridian, tonight's transits,
 * occultations, eclipses and shadow transits (each at its own local date and time, with
 * its length and altitude, and a badge on a double one) and the next nights' shadow
 * transits; Mars (NovaSolarMars) draws the disc with its own north up, as the Moon and
 * Jupiter are -- the polar cap tilted towards us, the shaded part and terminator, a dot
 * per named albedo feature, the pole and an arrow to celestial north -- with a tile for the central meridian (De and Ds in words in its
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
 * The Great Red Spot's longitude comes from one of three places, in this order: the
 * user's own measurement (localStorage nova_solar_grs), the value this instance fetched
 * from the catalog host (the page's solarGrs, null when it has none), and the default
 * shipped in nova_solar_jupiter.js. The tooltip names which one it is. Staleness is
 * judged by the value's own measurement date, so an offline instance with an old value
 * still shows the age warning.
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
        saturn: ['iapetusWest'],
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
        saturn: { key: null, tonight: null },
        saturnWidth: 0,
        cardView: 'normal',
        // The drawings' top layers and their markers by chip number, for showHotMarker()
        moonHot: { layer: null, markers: {} },
        marsHot: { layer: null, markers: {} },
        // What each card was last drawn for (its render's key), so a tick redraws nothing
        moonDrawn: null,
        jupiterDrawn: null,
        marsDrawn: null,
        saturnDrawn: null,
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

    /** The locale every date on the cards is written in: the page's language from
     *  <html lang> (Flask-Babel's own locale), English in Nova's European day-month order. */
    function pageLocale() {
        const lang = document.documentElement.lang || 'en';
        return lang === 'en' ? 'en-GB' : lang;
    }

    /** A calendar date (y, m, d) as 'Wed 4 Nov' -- or '1 Jun 2026' for a date with its
     *  year, such as a measurement's -- in the page's locale; one style for the Moon,
     *  Jupiter and Mars cards. */
    function cardDate(y, m, d, withYear) {
        const o = withYear ? {day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC'}
            : {weekday: 'short', day: 'numeric', month: 'short', timeZone: 'UTC'};
        try {
            return new Intl.DateTimeFormat(pageLocale(), o).format(new Date(Date.UTC(y, m - 1, d)));
        } catch (e) {
            return europeanDate(y + '-' + String(m).padStart(2, '0') + '-' + String(d).padStart(2, '0'));
        }
    }

    /** 'YYYY-MM-DD' as cardDate() writes it. */
    function cardDateISO(iso, withYear) {
        const [y, m, d] = String(iso).split('-').map(Number);
        return cardDate(y, m, d, withYear);
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
        renderSaturn();
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
            return new Intl.DateTimeFormat(pageLocale(),
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
        return cardDateISO(date, false);
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
        if (typeof NovaSolarSaturn !== 'undefined') return NovaSolarSaturn.VIEWS;
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
                renderSaturn();
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
        return cardDate(p.year, p.month, p.day, false) + ' ' + NovaAstro.formatHM(utcMs, tz);
    }

    /** Where one Galilean moon is tonight: which side of Jupiter, then what its symbol
     *  shows, in the symbol's own order (diagram() state): in Jupiter's shadow, hidden
     *  behind the disc, crossing its face, or beside it in front of or behind the planet. */
    function moonPlace(m, J) {
        const where = m.behindDisc ? J.hiddenBehind : m.onDisc ? J.onDisc : m.front ? J.inFront : J.behind;
        return [m.x >= 0 ? J.east : J.west, m.eclipsed ? J.eclipse : '', where]
            .filter(Boolean).join(' · ');
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
        if (e.inProgress) span(li, J.inProgress, 'solar-card-badge solar-card-badge-progress');
        if (e.double) span(li, J.double, 'solar-card-badge');
        li.title = [(J.eventTypes || {})[e.type], fill(J.minutes, {n: e.minutes}),
                    e.inProgress ? fill(J.inProgressHelp, {time: localDateTime(e.beganUtcMs)}) : '',
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
        if (s.inProgress) span(li, J.inProgress, 'solar-card-badge solar-card-badge-progress');
        if (s.double) span(li, J.double, 'solar-card-badge');
        li.title = [fill(J.shadowAt, {time: when}), s.inProgress ? J.inProgress : '',
                    s.double ? J.doubleHelp : ''].filter(Boolean).join('\n');
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

    /** The central longitude this instance fetched ({ lon, date, drift }), or null when it
     *  has none: the page is then on the shipped default, or on the user's own value.
     *  Sits between the two in the fallback chain, and the card reads it as `fallback`. */
    function serverGrs() {
        return (typeof solarGrs !== 'undefined' && solarGrs) ? solarGrs : null;
    }

    /** The GRS tile: tonight's predicted transits large, the longitude the prediction starts
     *  from and its date under them (the warning colour once that is over six months old),
     *  the next nights' transits in the tooltip; Edit and Reset inline. */
    function renderGrs(grs) {
        const J = i18n.jupiter || {};
        const G = J.grs || {};
        const tile = $('solar-jupiter-grs');
        const times = $('solar-jupiter-grs-times');
        // Tonight's times; else the next transit while Jupiter is up, marked as next
        const upcoming = !grs.tonight.length && grs.next.length ? grs.next[0] : null;
        times.textContent = grs.tonight.length ? grs.tonight.map(x => x.hm).join(' · ')
            : upcoming ? fill(G.nextOne, {time: localDateTime(upcoming.utcMs)}) : G.none || '';
        times.classList.toggle('solar-card-grs-none', !grs.tonight.length && !upcoming);
        times.classList.toggle('solar-card-grs-upcoming', !!upcoming);
        const refDate = cardDateISO(grs.refDate, true);
        $('solar-jupiter-grs-meta').textContent = fill(G.meta, {lon: grs.refLon.toFixed(1), date: refDate});
        tile.dataset.stale = grs.stale ? 'true' : 'false';
        $('solar-jupiter-grs-reset').hidden = !grs.user;
        tile.title = [
            G.help,
            grs.tonight.length ? fill(G.tonight, {times: grs.tonight.map(x => localDateTime(x.utcMs)).join(', ')}) : '',
            grs.next.length ? G.next + '\n' + grs.next.map(x => localDateTime(x.utcMs)).join('\n') : G.noNext,
            // Where the value came from, so a longitude that is not the user's own is
            // never mistaken for one: their own measurement, this instance's fetched
            // value, or the default shipped with this build.
            fill(grs.origin === 'user' ? G.user
                 : grs.origin === 'central' ? (G.central || '')
                 : G.source, {lon: grs.refLon.toFixed(1), date: refDate}),
            fill(G.drift, {rate: '+' + grs.driftPerMonth.toFixed(2)}),
            grs.stale ? G.stale : '',
            // Where a current value is found: always, and so right after the warning
            G.where,
        ].filter(Boolean).join('\n');
    }

    function grsForm(open) {
        $('solar-jupiter-grs-form').hidden = !open;
        $('solar-jupiter-grs-edit').hidden = open;
        $('solar-jupiter-grs-invalid').hidden = true;
        $('solar-jupiter-grs-future').hidden = true;
    }

    /** Today's date at the location, 'YYYY-MM-DD', on the real clock (a measurement can't
     *  have been made after it, whatever date is being simulated). */
    function locationToday() {
        const p = NovaAstro.localParts(Date.now(), String(state.inputs.location.timezone));
        return p.year + '-' + String(p.month).padStart(2, '0') + '-' + String(p.day).padStart(2, '0');
    }

    /** Edit, Save, Cancel and Reset. A value that cannot be kept (storage blocked) still
     *  holds for this visit. */
    function initGrs() {
        if (typeof NovaSolarJupiter === 'undefined') return;
        state.grsOverride = loadGrsOverride();
        grsForm(false);
        const redraw = () => { state.jupiterDrawn = null; renderJupiter(); };
        $('solar-jupiter-grs-edit').addEventListener('click', () => {
            // Prefilled with the effective reference, not with the default: a user editing
            // a value that came from the instance, or from the default, must see what the
            // card is actually predicting from.
            const ref = NovaSolarJupiter.grsReference(state.grsOverride, serverGrs());
            $('solar-jupiter-grs-lon').value = ref.lon.toFixed(1);
            $('solar-jupiter-grs-date').value = ref.date;
            $('solar-jupiter-grs-date').max = locationToday();
            grsForm(true);
        });
        $('solar-jupiter-grs-cancel').addEventListener('click', () => grsForm(false));
        $('solar-jupiter-grs-save').addEventListener('click', () => {
            const raw = String($('solar-jupiter-grs-lon').value).trim().replace(',', '.');
            const o = {lon: raw === '' ? NaN : Math.round(Number(raw) * 10) / 10,
                       date: String($('solar-jupiter-grs-date').value)};
            $('solar-jupiter-grs-invalid').hidden = true;
            $('solar-jupiter-grs-future').hidden = true;
            if (!NovaSolarJupiter.grsReference(o).user) {
                $('solar-jupiter-grs-invalid').hidden = false;
                return;
            }
            // 'YYYY-MM-DD' strings compare as dates
            if (o.date > locationToday()) {
                $('solar-jupiter-grs-future').hidden = false;
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
    const JUP_CHAR_PX = 7;                  // a Latin character at 12 px, about 0.58 em
    const JUP_LANES = [-15, 17, -31, 33, -47, 49, -63, 65];
    // Full-width characters, about 1 em each: CJK ideographs and punctuation, kana,
    // hangul, the full-width forms
    const WIDE_CHAR = /[\u1100-\u115f\u2e80-\ua4cf\uac00-\ud7a3\uf900-\ufaff\ufe30-\ufe4f\uff00-\uff60\uffe0-\uffe6]/;

    /** A label's width in pixels: the rendered text's own when the browser can measure it
     *  (the element is drawn), else an estimate by script. */
    function labelWidth(text, el) {
        if (el && typeof el.getComputedTextLength === 'function') {
            try {
                const w = el.getComputedTextLength();
                if (isFinite(w) && w > 0) return w;
            } catch (e) { /* not laid out: estimate */ }
        }
        let w = 0;
        for (const ch of String(text)) w += WIDE_CHAR.test(ch) ? JUP_LABEL_PX : JUP_CHAR_PX;
        return w;
    }

    /** A label's box, from its centre and width. */
    function labelBox(x, y, width) {
        const w = width + 4;
        return {x0: x - w / 2, x1: x + w / 2, y0: y - JUP_LABEL_PX / 2 - 1, y1: y + JUP_LABEL_PX / 2 + 1};
    }

    /** An arrow's path from (x0, y0) to its tip (x1, y1), with a head `len` long. */
    function arrowPath(x0, y0, x1, y1, len, fmt) {
        const dx = x1 - x0, dy = y1 - y0, n = Math.hypot(dx, dy) || 1;
        const bx = -dx / n, by = -dy / n, c = Math.cos(0.5), s = Math.sin(0.5);
        const h1 = [x1 + len * (bx * c - by * s), y1 + len * (bx * s + by * c)];
        const h2 = [x1 + len * (bx * c + by * s), y1 + len * (-bx * s + by * c)];
        return 'M ' + fmt(x0) + ' ' + fmt(y0) + ' L ' + fmt(x1) + ' ' + fmt(y1)
            + ' M ' + fmt(h1[0]) + ' ' + fmt(h1[1]) + ' L ' + fmt(x1) + ' ' + fmt(y1)
            + ' L ' + fmt(h2[0]) + ' ' + fmt(h2[1]);
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
        svgEl(svg, 'title', {}).textContent = [J.diagramAria, i18n.planetNorthUp, J.legendVisible,
            J.legendBehind, J.legendEclipsed, J.legendShadow, J.legendGrs].filter(Boolean).join('\n');
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

        // Celestial north first, an arrow out from the limb with its N past the tip, so the
        // names keep clear of both
        const ax0 = cx + d.north.x * (k + 3), ay0 = cy + d.north.y * (k + 3);
        const ax1 = cx + d.north.x * (k + 17), ay1 = cy + d.north.y * (k + 17);
        const arrow = svgEl(svg, 'g', {class: 'solar-card-north-arrow'});
        svgEl(arrow, 'path', {d: arrowPath(ax0, ay0, ax1, ay1, 5, px)});
        svgEl(arrow, 'title', {}).textContent = i18n.celestialNorth || '';
        const nx = cx + d.north.x * (k + 25), ny = cy + d.north.y * (k + 25);
        const north = svgEl(svg, 'text', {x: px(nx), y: px(ny), class: 'solar-card-north',
                                          'text-anchor': 'middle', 'dominant-baseline': 'central'});
        north.textContent = J.north || 'N';
        svgEl(north, 'title', {}).textContent = i18n.celestialNorth || '';
        const placed = [labelBox(nx, ny, labelWidth(north.textContent, north)),
            {x0: Math.min(ax0, ax1) - 3, x1: Math.max(ax0, ax1) + 3, y0: Math.min(ay0, ay1) - 3, y1: Math.max(ay0, ay1) + 3},
            {x0: cx - k - 2, x1: cx + k + 2, y0: cy - k - 2, y1: cy + k + 2}];

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
            // Drawn first, so the browser can measure it, then moved to its lane
            const label = svgEl(svg, 'text', {x: px(o.x), y: px(o.y), class: 'solar-card-moon-label',
                                              'data-moon': o.m.name, 'text-anchor': 'middle', 'dominant-baseline': 'central'});
            label.textContent = o.name;
            const width = labelWidth(o.name, label);
            const half = (width + 4) / 2;
            const lx = Math.min(W - half, Math.max(half, o.x));
            // The nearest free lane; when none is free, the one that covers the fewest
            // (the disc and north are placed[0..2], so each counts like a name)
            let spot = null;
            for (const dy of JUP_LANES) {
                const y = Math.min(H - JUP_LABEL_PX / 2, Math.max(JUP_LABEL_PX / 2, o.y + dy));
                const b = labelBox(lx, y, width);
                const hits = placed.filter(p => overlaps(p, b)).length;
                if (!spot || hits < spot.hits) spot = {y: y, b: b, hits: hits};
                if (!hits) break;
            }
            placed.push(spot.b);
            label.setAttribute('x', String(px(lx)));
            label.setAttribute('y', String(px(spot.y)));
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
                state.jupiter.grs = NovaSolarJupiter.grs(opts, state.grsOverride, serverGrs());
                state.jupiter.grsKey = grsKey;
            }
            t = state.jupiter.tonight;
            next = state.jupiter.next;
            grs = state.jupiter.grs;
            const ref = NovaSolarJupiter.grsReference(state.grsOverride, serverGrs());
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

    /** Mars's disc tonight with its own north up: the polar cap tilted towards us, the
     *  unlit part, the terminator, a dot per named feature, a tick at the pole, and an
     *  arrow to celestial north, its N in HTML over the drawing at a fixed size. */
    function marsDiagram(t) {
        const M = i18n.mars || {};
        const box = $('solar-mars-diagram');
        box.innerHTML = '';
        const d = NovaSolarMars.diagram(t.geometry, state.cardView, t.top);
        const svg = svgEl(box, 'svg', {
            viewBox: '-1.3 -1.3 2.6 2.6', role: 'img', class: 'solar-card-svg solar-mars-svg',
            'aria-label': M.diagramAria || '',
        });
        svgEl(svg, 'title', {}).textContent = [M.diagramAria, i18n.planetNorthUp].filter(Boolean).join('\n');
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

        // Celestial north: an arrow out from the limb, its N past the tip in HTML (a fixed
        // 12 px at any drawing size)
        const r3 = v => Math.round(v * 1000) / 1000;
        const arrow = svgEl(svg, 'g', {class: 'solar-card-north-arrow'});
        svgEl(arrow, 'path', {d: arrowPath(d.north.x * 1.03, d.north.y * 1.03,
                                           d.north.x * 1.15, d.north.y * 1.15, 0.05, r3)});
        svgEl(arrow, 'title', {}).textContent = i18n.celestialNorth || '';
        const north = span(box, M.north || 'N', 'solar-card-north-label');
        north.style.left = ((d.north.x * 1.24 + 1.3) / 2.6 * 100).toFixed(2) + '%';
        north.style.top = ((d.north.y * 1.24 + 1.3) / 2.6 * 100).toFixed(2) + '%';
        north.title = i18n.celestialNorth || '';
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

    // ------------------------------------------------------ Saturn card

    // Saturn's figure is the Jupiter strip's shape -- a wide drawing at the width it is
    // shown, its names at a fixed 12 px and placed in the same lanes -- drawn in Saturn's own
    // radii, where the globe is 1 and the A ring's outer edge 2.2666, so the strip reaches
    // NovaSolarSaturn.STRIP_RS of them each side of the centre.
    const SAT_DOT_PX = 4;                    // a moon the drawing names
    const SAT_SMALL_PX = 2.5;                // Mimas and Enceladus, whose dots stay small
    const SAT_CHEVRON_PX = 5;                // a moon beyond the edge of the figure
    // The two smallest moons. Their dots are always the small one, and their names are set
    // only where the lane search finds a lane free outright: at this scale a name has to be
    // earned, and Titan's, Rhea's and the rest are seen to first.
    const SAT_SMALL = ['Mimas', 'Enceladus'];
    // How far off the ring plane the figure reaches, in Saturn radii. The moons ride the ring
    // plane, so this is what decides whether one is drawn where it is or marked at the edge.
    const SAT_PLANE_RS = 2;
    // The figure's height: tall enough for a moon SAT_PLANE_RS off the plane to be drawn as a
    // dot there -- its whole marker, plus the 2 px the figure keeps clear of its own edge --
    // at the scale the card's own width sets, and never shorter than the Jupiter strip. The
    // cap is where the card stops growing: past it the widest cards mark those moons at the
    // edge, as they always did.
    const SAT_MAX_HEIGHT = 240;
    // The Jupiter lanes, and the further ones a moon in front of or behind the globe needs:
    // its own name has to clear the globe, which is half the figure tall. The list is scanned
    // in order and the first free lane wins, so the extra ones only ever come into play where
    // the Jupiter list runs out.
    const SAT_LANES = JUP_LANES.concat([-79, 81, -95, 97, -111, 113]);
    const SAT_FAR_CLIP = 'solar-saturn-far';
    const SAT_NEAR_CLIP = 'solar-saturn-near';
    const SAT_GLOBE_CLIP = 'solar-saturn-globe';
    const SAT_FRONT_CLIP = 'solar-saturn-front';

    /** What one of Saturn's moons is doing tonight, in the module's own order of states: in
     *  front of the globe (`onDisc` when it is inside the limb as well, which is a transit),
     *  hidden behind it -- only when it really is within the disc and behind it -- or simply
     *  on the far side of its orbit, which is where most of the eight spend most of a night
     *  and is not the same thing as hidden. */
    function saturnMoonWhere(m) {
        const P = (i18n.saturn || {}).moonPlace || {};
        return P[m.onDisc ? 'onDisc' : m.behindDisc ? 'hidden' : m.inFront ? 'front' : 'far'] || '';
    }

    /** A moon's own lines, the same on the drawing and in its chip: how bright it is, which
     *  side of Saturn it is on -- the sky's side, since the drawing's left and right are
     *  Saturn's own frame -- what it is doing, how far out it is and, for Iapetus, which of
     *  its two different sides is towards us, and where that comes from. */
    function saturnMoonTip(m, S) {
        const side = (S.moonSide || {})[m.side] || '';
        return [m.mag === undefined ? '' : fill(S.moonMag, {mag: m.mag.toFixed(1)}),
                [side, saturnMoonWhere(m)].filter(Boolean).join(' · '),
                fill(S.moonRadii, {rs: m.dist.toFixed(1)}),
                m.brightness === 'bright' ? S.iapetusBright
                    : m.brightness === 'faint' ? S.iapetusFaint : '',
                m.brightness ? S.iapetusHelp : '',
                m.brightness ? S.iapetusSource : ''].filter(Boolean).join('\n');
    }

    /** An instant as the location's own date with its year and no time: for the answers that
     *  are about a day and not a moment (Titan's next season, whose crossings are not
     *  calculated, so no time of day is claimed). */
    function localDate(utcMs) {
        const p = NovaAstro.localParts(utcMs, String(state.inputs.location.timezone));
        return cardDate(p.year, p.month, p.day, true);
    }

    /** The Rings tile's sparkline: B over the next two years, the edge-on line at 0 across
     *  the middle, and the two-year direction as an arrow at the curve's own end -- away from
     *  the edge-on line is opening, towards it closing, which is the way the curve runs while
     *  the tilt keeps to itself. The scale is the window's largest |B|, never under a degree,
     *  so a year with the rings nearly closed draws as the flat line it is. The width is the
     *  tile's own content width, measured, because a sparkline 66 px wide cannot be read; the
     *  66 px the card was written against is the fallback for a figure that cannot be
     *  measured at all, the test harness's own. */
    function ringSpark(row, months, S, dir) {
        const W = Math.max(66, Math.round(row.clientWidth || 0) || 66), H = 22, pad = 4;
        const svg = svgEl(row, 'svg', {viewBox: '0 0 ' + W + ' ' + H, class: 'solar-saturn-spark',
                                       role: 'img', 'aria-label': S.sparkAria || '', focusable: 'false'});
        const px = v => Math.round(v * 10) / 10;
        const top = Math.max(1, months.reduce((v, m) => Math.max(v, Math.abs(m.B)), 0));
        const n = months.length;
        const x = i => pad + (W - 2 * pad) * i / Math.max(1, n - 1);
        const y = b => H / 2 - (H / 2 - pad) * (b / top);
        svgEl(svg, 'path', {d: 'M ' + pad + ' ' + px(H / 2) + ' H ' + (W - pad),
                            class: 'solar-saturn-spark-zero'});
        svgEl(svg, 'path', {class: 'solar-saturn-spark-line',
            d: months.map((m, i) => (i ? 'L ' : 'M ') + px(x(i)) + ' ' + px(y(m.B))).join(' ')});
        if (n > 1) {
            // The two years' direction, at the curve's own end: |B| growing runs away from the
            // edge-on line whichever face is towards us, |B| shrinking runs back to it
            const away = months[n - 1].B >= 0 ? -1 : 1;
            const ty = (dir === 'closing' ? -away : away) * 5;
            const tip = [x(n - 1), y(months[n - 1].B)];
            svgEl(svg, 'path', {class: 'solar-saturn-spark-head', d:
                'M ' + px(tip[0]) + ' ' + px(tip[1] + ty)
                + ' L ' + px(tip[0] - 3) + ' ' + px(tip[1])
                + ' L ' + px(tip[0] + 3) + ' ' + px(tip[1]) + ' Z'});
        }
        return svg;
    }

    function saturnTiles(t) {
        const S = i18n.saturn || {};
        const tiles = $('solar-saturn-tiles');
        tiles.innerHTML = '';
        const r = t.rings;
        const months = r.months || [];
        // Which way the rings are going over the two years the sparkline draws: |B| at the
        // end of the window against |B| tonight. The module's own trend is a month's, and the
        // two can differ -- the tilt wobbles inside the longer swing -- so the tile says the
        // two-year one and the tooltip keeps both, the month's as the wobble it is.
        const dir = months.length && Math.abs(months[months.length - 1].B) < Math.abs(months[0].B)
            ? 'closing' : 'opening';
        const word = (S.ringTrend || {})[dir] || '';
        const face = (S.faceEarth || {})[r.face] || '';
        const name = (S.faceShort || {})[r.face] || '';
        const sun = (S.faceSun || {})[r.sunFace] || '';
        const wobble = r.trend && r.trend !== dir ? S.ringWobble : '';
        const tile = cardTile(tiles, 'solar-card', S.rings,
            Math.abs(r.B).toFixed(1) + '°' + (name ? ' · ' + name : ''),
            [S.ringsHelp, fill(S.ringBprime, {bprime: signed(r.Bprime, 1)}), face,
             fill(S.ringLit, {face: sun}),
             fill(S.ringSpan, {arcsec: r.aArcsec.toFixed(1)}),
             fill(S.aRingEdge, {rs: r.radiusRs.toFixed(2)}),
             fill(S.ringTrendLong, {trend: word}), wobble].filter(Boolean).join('\n'));
        // The curve is the tile's content width, so it takes a row of its own under the value
        ringSpark(span(tile, '', 'solar-card-tile-row solar-card-tile-row-spark'), months, S, dir);
        span(tile, word, 'solar-card-tile-note');
        if (!r.sameFace) span(tile, S.ringFacesDiffer, 'solar-card-tile-note');
    }

    /** Saturn tonight as a system, drawn as the Jupiter strip is: the globe in the centre,
     *  the ring system at tonight's opening angle, the band the rings' shadow cuts across the
     *  globe, the moons, and an arrow to celestial north. The rings' far half is drawn behind
     *  the globe and their near half in front of it, so the planet hides them where they run
     *  behind it: a point of the ring is in front of Saturn when B and its own side of the
     *  major axis have opposite signs, which is which half of the drawing the clip rects say. */
    function saturnDiagram(t) {
        const S = i18n.saturn || {};
        const box = $('solar-saturn-diagram');
        box.innerHTML = '';
        const W = Math.max(240, Math.round(box.clientWidth || 0) || 720);
        const cx = W / 2;
        const k = (cx - 8) / NovaSolarSaturn.STRIP_RS;        // pixels per Saturn radius
        // The height follows the scale the card's width sets, so the ring plane and the moons
        // on it get the room their own markers need at that scale
        const H = Math.round(Math.min(SAT_MAX_HEIGHT, Math.max(JUP_HEIGHT,
            2 * (SAT_PLANE_RS * k + SAT_DOT_PX + 2))));
        box.style.height = H + 'px';                          // the SVG fills the box exactly
        const cy = H / 2;
        const px = v => Math.round(v * 100) / 100;
        const r = t.rings;
        // The figure's own moons, in the module's own list and order, so each drawn entry
        // here has its own row of the engine's sky-frame ones
        const moons = t.moons.filter(m => NovaSolarSaturn.DRAWN.indexOf(m.name) >= 0);
        const d = NovaSolarSaturn.diagram(t.geometry, state.cardView, moons, r.radiusRs);
        const rw = r.radiusRs * k;
        const rh = rw * Math.abs(Math.sin(r.B * Math.PI / 180));   // the rings' own half-height
        const sy = state.cardView === 'inverted' ? 1 : -1;
        const nearBelow = r.B * sy < 0;
        const svg = svgEl(box, 'svg', {
            width: W, height: H, viewBox: '0 0 ' + W + ' ' + H, role: 'img',
            class: 'solar-card-svg solar-saturn-svg', 'aria-label': S.diagramAria || '',
            'data-scale': Math.round(k * 1e6) / 1e6,
        });
        svgEl(svg, 'title', {}).textContent = [S.diagramAria, i18n.planetNorthUp, S.legendFilled,
            S.legendHollow, S.legendEdge, S.legendShadow, S.legendSmall].filter(Boolean).join('\n');

        // The half-plane each half of the rings is clipped to, the globe's own clip for the
        // shadow, and the globe's near half-disc for the rings that cross it -- the last two
        // in the figure's radii, where they are used
        const clip = id => svgEl(svg, 'clipPath', {id: id, 'clipPathUnits': 'userSpaceOnUse'});
        svgEl(clip(SAT_FAR_CLIP), 'rect', {x: -2, y: nearBelow ? -2 : cy, width: W + 4, height: cy + 2});
        svgEl(clip(SAT_NEAR_CLIP), 'rect', {x: -2, y: nearBelow ? cy : -2, width: W + 4, height: cy + 2});
        svgEl(clip(SAT_GLOBE_CLIP), 'circle', {cx: 0, cy: 0, r: 1});
        // From the globe's left limb to its right, at the globe's own radius: clockwise on
        // the page passes over the top, so sweep 1 is the half above the centre line and 0
        // the one below it
        svgEl(clip(SAT_FRONT_CLIP), 'path', {d: 'M ' + px(cx - k) + ' ' + cy + ' A ' + px(k)
            + ' ' + px(k) + ' 0 ' + (nearBelow ? 0 : 1) + ' 1 ' + px(cx + k) + ' ' + cy + ' Z'});

        // The figure is drawn in Saturn radii: one group per layer, in the order they cover
        // each other -- the far rings, the globe with the shadow on it, the near rings
        const scaled = parent => svgEl(parent, 'g', {transform: 'translate(' + cx + ' ' + cy + ') scale(' + k + ')'});
        const ringPaths = parent => d.rings.forEach(x => svgEl(parent, 'path', {d: x.d,
            class: 'solar-saturn-ring solar-saturn-ring-' + x.key, 'vector-effect': 'non-scaling-stroke'}));
        ringPaths(scaled(svgEl(svg, 'g', {'clip-path': 'url(#' + SAT_FAR_CLIP + ')'})));
        const globe = scaled(svg);
        svgEl(globe, 'circle', {cx: 0, cy: 0, r: 1, class: 'solar-saturn-globe',
                                'vector-effect': 'non-scaling-stroke'});
        if (d.shadow) {
            svgEl(svgEl(globe, 'g', {'clip-path': 'url(#' + SAT_GLOBE_CLIP + ')'}),
                  'path', {d: d.shadow, class: 'solar-saturn-shadow'});
        }
        ringPaths(scaled(svgEl(svg, 'g', {'clip-path': 'url(#' + SAT_NEAR_CLIP + ')'})));
        // The near half once more, on the globe's own near half-disc: over the globe the
        // sides' strengths wash out against the body colour, so the band the rings cut across
        // the planet is drawn in the rings' own three colours at full strength. The shadow the
        // rings cast stays what the globe's own layer put there, a band of its own beside
        // them, and the rings cover it only where they really do pass in front of it.
        ringPaths(scaled(svgEl(svg, 'g', {class: 'solar-saturn-front',
                                          'clip-path': 'url(#' + SAT_FRONT_CLIP + ')'})));

        // The moons: a dot at its own place for the ones the figure reaches, a chevron where
        // the line to it leaves the figure for the ones it does not, and a smaller unnamed
        // dot for Mimas and Enceladus. Nothing is clamped: a moon is drawn where it is or
        // marked at the edge its own line leaves through, never at a place it does not have.
        // A moon's line across the drawing runs far outside a strip under two radii tall --
        // the ring plane tilts the moons out of it, more the further out they are -- and the
        // distance that says how far out is the tooltip's.
        const leave = (dx, dy, size) => {
            const inset = size + 2;
            const tx = dx === 0 ? Infinity : ((dx > 0 ? W - inset : inset) - cx) / dx;
            const ty = dy === 0 ? Infinity : ((dy > 0 ? H - inset : inset) - cy) / dy;
            const t = Math.min(tx, ty);
            return {x: cx + t * dx, y: cy + t * dy};
        };
        const spots = d.moons.map((m, i) => {
            const sky = moons[i];
            const small = SAT_SMALL.indexOf(m.name) >= 0;
            const rad = small ? SAT_SMALL_PX : SAT_DOT_PX;
            const size = m.off ? SAT_CHEVRON_PX : rad;
            const dx = m.x * k, dy = m.y * k;
            const held = !m.off && Math.abs(dx) <= cx - size - 2 && Math.abs(dy) <= cy - size - 2;
            const at = held ? {x: cx + dx, y: cy + dy} : leave(dx, dy, size);
            const g = svgEl(svg, 'g', {class: 'solar-saturn-moon solar-saturn-moon-' +
                (sky.behindDisc ? 'behind' : 'visible') + (small ? ' solar-saturn-moon-small' : ''),
                'data-moon': m.name, 'data-side': sky.side, 'data-brightness': sky.brightness || ''});
            let box;
            if (held) {
                svgEl(g, 'circle', {cx: px(at.x), cy: px(at.y), r: rad});
                box = {x0: at.x - rad - 1, x1: at.x + rad + 1,
                       y0: at.y - rad - 1, y1: at.y + rad + 1};
            } else {
                // The chevron's tip is where the line leaves the figure and it points the way
                // the moon is: its own line drawn, not a fixed mark at the figure's edge
                const len = Math.hypot(dx, dy) || 1, ux = dx / len, uy = dy / len;
                const back = [at.x - ux * SAT_CHEVRON_PX, at.y - uy * SAT_CHEVRON_PX];
                const arms = [[back[0] + uy * SAT_CHEVRON_PX, back[1] - ux * SAT_CHEVRON_PX],
                              [back[0] - uy * SAT_CHEVRON_PX, back[1] + ux * SAT_CHEVRON_PX]];
                svgEl(g, 'path', {d: 'M ' + px(arms[0][0]) + ' ' + px(arms[0][1])
                    + ' L ' + px(at.x) + ' ' + px(at.y)
                    + ' L ' + px(arms[1][0]) + ' ' + px(arms[1][1])});
                const xs = arms.map(a => a[0]).concat([at.x]), ys = arms.map(a => a[1]).concat([at.y]);
                box = {x0: Math.min(...xs) - 1, x1: Math.max(...xs) + 1,
                       y0: Math.min(...ys) - 1, y1: Math.max(...ys) + 1};
            }
            svgEl(g, 'title', {}).textContent = m.name + '\n' + saturnMoonTip(sky, S);
            return {m: m, x: at.x, y: at.y, box: box};
        });

        // Celestial north, out past the ring system, so the names keep clear of both
        const out = Math.max(k, rh);
        const ax0 = cx + d.north.x * (out + 3), ay0 = cy + d.north.y * (out + 3);
        const ax1 = cx + d.north.x * (out + 17), ay1 = cy + d.north.y * (out + 17);
        const arrow = svgEl(svg, 'g', {class: 'solar-card-north-arrow'});
        svgEl(arrow, 'path', {d: arrowPath(ax0, ay0, ax1, ay1, 5, px)});
        svgEl(arrow, 'title', {}).textContent = i18n.celestialNorth || '';
        const nx = cx + d.north.x * (out + 25), ny = cy + d.north.y * (out + 25);
        const north = svgEl(svg, 'text', {x: px(nx), y: px(ny), class: 'solar-card-north',
                                          'text-anchor': 'middle', 'dominant-baseline': 'central'});
        north.textContent = S.north || 'N';
        svgEl(north, 'title', {}).textContent = i18n.celestialNorth || '';
        // The arrow, the globe and the ring system first, then the moons' own markers: a name
        // belongs beside its moon, never on top of it, and never on the planet, on the rings
        // or on another name either
        const placed = [labelBox(nx, ny, labelWidth(north.textContent, north)),
            {x0: Math.min(ax0, ax1) - 3, x1: Math.max(ax0, ax1) + 3,
             y0: Math.min(ay0, ay1) - 3, y1: Math.max(ay0, ay1) + 3},
            {x0: cx - rw - 2, x1: cx + rw + 2, y0: cy - rh - 2, y1: cy + rh + 2},
            {x0: cx - k - 2, x1: cx + k + 2, y0: cy - k - 2, y1: cy + k + 2}]
            .concat(spots.map(o => o.box));

        // One name in the nearest lane that will hold it, in the lanes the Jupiter strip uses
        // and the further ones SAT_LANES adds. `required` names -- everything but Mimas and
        // Enceladus -- take the best lane there is; the two small ones are drawn only where a
        // lane is free outright, and the text element is taken away again where none is.
        const nameMoon = (o, required) => {
            const label = svgEl(svg, 'text', {x: px(o.x), y: px(o.y), class: 'solar-card-moon-label',
                                              'data-moon': o.m.name, 'text-anchor': 'middle',
                                              'dominant-baseline': 'central'});
            label.textContent = o.m.name;                  // drawn first, so it can be measured
            const width = labelWidth(o.m.name, label);
            const half = (width + 4) / 2;
            const lx = Math.min(W - half, Math.max(half, o.x));
            let spot = null;
            for (const dy of SAT_LANES) {
                const y = Math.min(H - JUP_LABEL_PX / 2, Math.max(JUP_LABEL_PX / 2, o.y + dy));
                const b = labelBox(lx, y, width);
                // A name never sits on its own moon: a lane that covers its own marker loses
                // to any lane that does not, however many other names it covers. For a moon
                // in the top or bottom rows the clamp pulls the first lane onto it, so its
                // own marker has to weigh more than the rest.
                const score = placed.filter(p => overlaps(p, b)).length
                    + (overlaps(o.box, b) ? 100 : 0);
                if (!spot || score < spot.score) spot = {y: y, b: b, score: score};
                if (!score) break;
            }
            if (!required && spot.score) { svg.removeChild(label); return; }
            placed.push(spot.b);
            label.setAttribute('x', String(px(lx)));
            label.setAttribute('y', String(px(spot.y)));
        };
        // The moons the card always names, the nearest the planet first, and then the two
        // small ones in the room that is left
        const nearest = (a, b) => Math.abs(a.m.x) - Math.abs(b.m.x);
        const small = o => SAT_SMALL.indexOf(o.m.name) >= 0;
        spots.filter(o => !small(o)).sort(nearest).forEach(o => nameMoon(o, true));
        spots.filter(small).sort(nearest).forEach(o => nameMoon(o, false));
        state.saturnWidth = W;
    }

    /** The Saturn card, while Saturn is selected; computed once per night, as the Moon's. */
    function renderSaturn() {
        const box = $('solar-saturn');
        if (typeof NovaSolarSaturn === 'undefined' || !state.result || state.selected !== 'saturn') {
            box.hidden = true;
            return;
        }
        box.hidden = false;
        const S = i18n.saturn || {};
        let t, drawn;
        try {
            const key = calendarKey();
            const width = Math.round($('solar-saturn-diagram').clientWidth || 0);
            drawn = JSON.stringify([key, simDate(), state.cardView, document.documentElement.lang || '', width]);
            if (state.saturnDrawn === drawn) return;
            if (state.saturn.key !== key) {
                const opts = {inputs: state.inputs, nowMs: Date.now(), simDate: simDate()};
                state.saturn = {key: null, tonight: NovaSolarSaturn.tonight(opts)};
                state.saturn.key = key;
            }
            t = state.saturn.tonight;
            saturnDiagram(t);
            saturnTiles(t);
        } catch (e) {
            console.error('Solar System: could not calculate the Saturn card', e);
            state.saturn = {key: null, tonight: null};
            state.saturnDrawn = null;
            $('solar-saturn-title').textContent = S.title || bodyName('saturn');
            showCardError('saturn', S.error);
            return;
        }
        showCardError('saturn', null);
        $('solar-saturn-title').textContent = (S.title || bodyName('saturn')) + ' · '
            + fill(S.highestAt, {time: t.hm});

        $('solar-saturn-body').classList.toggle('solar-card-muted', t.muted);

        const empty = $('solar-saturn-empty');
        const reason = t.muted ? (t.mutedReason === 'notUp' ? (S.empty || {}).notUp : i18n.belowThreshold) : '';
        empty.textContent = reason || '';
        empty.hidden = !reason;

        // The transit answer: one sentence, because Titan's crossing is the only one worth a
        // sentence. The moons that do cross the disc this season are far too small to be seen
        // against it, so there is no list; the season Titan next crosses in, and the small
        // moons that cross this one -- from the data, not from the list the card was written
        // against -- are in the sentence's own tooltip, with what "crossing the disc" means.
        const titan = t.transits.titan;
        const others = t.transits.crossing.filter(x => x.name !== 'Titan');
        const none = $('solar-saturn-transits-none');
        none.textContent = titan ? S.titanCross : S.noTitan;
        none.title = (titan
            ? [fill(S.transitItem, {rs: titan.closestRs.toFixed(2), date: localDateTime(titan.utcMs)})]
            : [t.transits.titanNext ? fill(S.titanNext, {date: localDate(t.transits.titanNext.utcMs)}) : '',
               others.length ? fill(S.smallCross, {names: others.map(x => x.name).join(', ')}) : ''])
            .concat([S.transitHelp]).filter(Boolean).join('\n');

        // The credit line is the two sources' short names; the whole of them is in its tooltip
        const source = typeof NovaSolarSaturn !== 'undefined' ? NovaSolarSaturn.SOURCE : null;
        const credit = $('solar-saturn-credit');
        credit.textContent = source ? fill(S.credit, {source: source.short}) : '';
        credit.title = source ? source.credit : '';
        credit.hidden = !source;
        state.saturnDrawn = drawn;
    }

    /** The strip is drawn at the width it is shown: a new width draws it again. */
    function initSaturnResize() {
        if (typeof ResizeObserver === 'undefined') return;
        const box = $('solar-saturn-diagram');
        new ResizeObserver(() => {
            const W = Math.round(box.clientWidth || 0);
            if (!W || W === state.saturnWidth || $('solar-saturn').hidden) return;
            renderSaturn();
        }).observe(box);
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
            renderSaturn();
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
            // And Iapetus's western elongations, nine of them in the next two years: the
            // nights its bright side is turned towards us. Memoised on this key the same way.
            if (typeof NovaSolarSaturn !== 'undefined') {
                try {
                    result.rows = result.rows.concat(NovaSolarSaturn.calendarRows(opts).rows)
                        .sort((a, b) => a.utcMs - b.utcMs);
                } catch (e) {
                    console.error('Solar System: could not calculate the Saturn Calendar events', e);
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
        initSaturnResize();
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
