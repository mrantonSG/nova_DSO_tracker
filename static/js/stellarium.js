/**
 * Nova DSO Tracker – Stellarium Remote Control Integration
 *
 * Client-side JS that talks directly to the user's local Stellarium instance
 * via the Remote Control plugin HTTP API (default http://localhost:8090).
 *
 * Settings are persisted server-side per user via /api/v1/stellarium/settings.
 * All Stellarium commands are sent from the browser → user's Stellarium (no proxy).
 */
(function () {
    'use strict';

    var DEFAULTS = { host: 'localhost', port: 8090, enabled: false };
    var _settings = null;

    /* ── helpers ────────────────────────────────────────────────── */

    function _baseUrl() {
        if (!_settings) return null;
        return 'http://' + _settings.host + ':' + _settings.port;
    }

    function _timeout(ms) {
        var ctrl = new AbortController();
        var id = setTimeout(function () { ctrl.abort(); }, ms);
        return { signal: ctrl.signal, clear: function () { clearTimeout(id); } };
    }

    /* ── settings ──────────────────────────────────────────────── */

    function loadSettings() {
        return fetch('/api/v1/stellarium/settings', { credentials: 'same-origin' })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (json) {
                _settings = (json && json.data) ? json.data : _copy(DEFAULTS);
                return _settings;
            })
            .catch(function () {
                _settings = _copy(DEFAULTS);
                return _settings;
            });
    }

    function getSettings() {
        return _settings || _copy(DEFAULTS);
    }

    function isEnabled() {
        return !!(_settings && _settings.enabled);
    }

    function saveSettings(host, port, enabled) {
        return fetch('/api/v1/stellarium/settings', {
            method: 'PUT',
            credentials: 'same-origin',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ host: host, port: parseInt(port, 10), enabled: !!enabled })
        })
        .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
        .then(function (res) {
            if (res.ok) {
                _settings = res.data.data;
                return { success: true };
            }
            return { success: false, message: res.data.error || 'Failed to save' };
        })
        .catch(function () {
            return { success: false, message: 'Network error saving settings' };
        });
    }

    /* ── connection test ───────────────────────────────────────── */

    function testConnection(host, port) {
        var url = 'http://' + (host || _settings.host) + ':' + (port || _settings.port);
        var t = _timeout(5000);

        return fetch(url + '/api/main/status', { signal: t.signal, mode: 'cors' })
            .then(function (r) {
                t.clear();
                if (r.ok) return { success: true, message: 'Connected to Stellarium!' };
                return { success: false, message: 'Stellarium returned status ' + r.status };
            })
            .catch(function (err) {
                t.clear();
                if (err.name === 'AbortError') {
                    return { success: false, message: 'Connection timed out (5 s).' };
                }
                /* CORS block or network error – try no-cors probe */
                return _probeNoCors(url);
            });
    }

    /**
     * Fallback probe: send a no-cors request.  If it resolves the server is
     * likely up (CORS just blocks the response).  If it rejects → truly down.
     */
    function _probeNoCors(url) {
        var t = _timeout(5000);
        return fetch(url + '/api/main/status', { signal: t.signal, mode: 'no-cors' })
            .then(function () {
                t.clear();
                return {
                    success: true,
                    message: 'Stellarium appears reachable (CORS blocked full check). '
                           + 'Enable CORS in Stellarium → Remote Control plugin settings for best results.',
                    corsWarning: true
                };
            })
            .catch(function (err) {
                t.clear();
                if (err.name === 'AbortError') {
                    return { success: false, message: 'Connection timed out (5 s).' };
                }
                return { success: false, message: 'Cannot reach Stellarium at ' + url + '.' };
            });
    }

    /* ── commands ───────────────────────────────────────────────── */

    /**
     * Focus / GoTo: tell Stellarium to centre on the named object.
     * Uses mode:"no-cors" so the command fires even without CORS headers.
     */
    function focusObject(objectName) {
        if (!_baseUrl() || !objectName) {
            return Promise.resolve({ success: false, message: 'Stellarium not configured.' });
        }
        var t = _timeout(5000);
        return fetch(_baseUrl() + '/api/main/focus', {
            method: 'POST',
            mode: 'no-cors',
            signal: t.signal,
            headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
            body: 'target=' + encodeURIComponent(objectName) + '&mode=center'
        })
        .then(function () {
            t.clear();
            return { success: true, message: 'Sent "' + objectName + '" to Stellarium.' };
        })
        .catch(function (err) {
            t.clear();
            if (err.name === 'AbortError') {
                return { success: false, message: 'Timed out reaching Stellarium.' };
            }
            return { success: false, message: 'Cannot reach Stellarium.' };
        });
    }

    /* ── UI helpers ─────────────────────────────────────────────── */

    /**
     * Call once on DOMContentLoaded to show / hide every element with
     * class "nova-stellarium-action" based on the user's Stellarium settings.
     */
    function initPageActions() {
        return loadSettings().then(function () {
            var els = document.querySelectorAll('.nova-stellarium-action');
            for (var i = 0; i < els.length; i++) {
                els[i].style.display = isEnabled() ? '' : 'none';
            }
            /* also handle the legacy id */
            var legacy = document.getElementById('open-in-stellarium');
            if (legacy) legacy.style.display = isEnabled() ? '' : 'none';
        });
    }

    /* ── private ────────────────────────────────────────────────── */

    function _copy(o) { return JSON.parse(JSON.stringify(o)); }

    /* ── public API ─────────────────────────────────────────────── */

    window.NovaStellarium = {
        loadSettings:   loadSettings,
        getSettings:    getSettings,
        isEnabled:      isEnabled,
        saveSettings:   saveSettings,
        testConnection: testConnection,
        focusObject:    focusObject,
        initPageActions: initPageActions
    };
})();
