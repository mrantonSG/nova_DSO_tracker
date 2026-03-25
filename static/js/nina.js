/**
 * Nova DSO Tracker – N.I.N.A. (Nighttime Imaging 'N' Astronomy) Integration
 *
 * Client-side JS that talks directly to the user's local NINA instance
 * via the Advanced API plugin (https://github.com/christian-photo/ninaAPI).
 * Default endpoint: http://localhost:1888/v2/api
 *
 * Settings are persisted server-side per user via /api/v1/nina/settings.
 * All NINA commands are sent from the browser → user's NINA (no proxy).
 *
 * Key difference from Stellarium: NINA takes RA/Dec coordinates in decimal degrees,
 * not object names. The coordinates must be J2000 epoch.
 */
(function () {
    'use strict';

    var DEFAULTS = { host: 'localhost', port: 1888, enabled: false };
    var _settings = null;

    /* ── helpers ────────────────────────────────────────────────── */

    function _baseUrl() {
        if (!_settings) return null;
        return 'http://' + _settings.host + ':' + _settings.port + '/v2/api';
    }

    function _timeout(ms) {
        var ctrl = new AbortController();
        var id = setTimeout(function () { ctrl.abort(); }, ms);
        return { signal: ctrl.signal, clear: function () { clearTimeout(id); } };
    }

    /* ── settings ──────────────────────────────────────────────── */

    function loadSettings() {
        return fetch('/api/v1/nina/settings', { credentials: 'same-origin' })
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
        return fetch('/api/v1/nina/settings', {
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

    /**
     * Test connection to NINA's Advanced API.
     * We try the /application/get-version endpoint which should always work.
     */
    function testConnection(host, port) {
        var url = 'http://' + (host || _settings.host) + ':' + (port || _settings.port) + '/v2/api';
        var t = _timeout(5000);

        return fetch(url + '/application/get-version', { signal: t.signal, mode: 'cors' })
            .then(function (r) {
                t.clear();
                if (r.ok) {
                    return r.json().then(function (data) {
                        if (data.Success) {
                            return { success: true, message: 'Connected to NINA! Version: ' + data.Response };
                        }
                        return { success: false, message: 'NINA API returned error: ' + (data.Error || 'Unknown') };
                    });
                }
                return { success: false, message: 'NINA returned status ' + r.status };
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
     * Fallback probe: send a no-cors request. If it resolves the server is
     * likely up (CORS just blocks the response). If it rejects → truly down.
     */
    function _probeNoCors(url) {
        var t = _timeout(5000);
        return fetch(url + '/application/get-version', { signal: t.signal, mode: 'no-cors' })
            .then(function () {
                t.clear();
                return {
                    success: true,
                    message: 'NINA appears reachable (CORS blocked full check). '
                           + 'Commands should still work.',
                    corsWarning: true
                };
            })
            .catch(function (err) {
                t.clear();
                if (err.name === 'AbortError') {
                    return { success: false, message: 'Connection timed out (5 s).' };
                }
                return { success: false, message: 'Cannot reach NINA at ' + url + '. Make sure the Advanced API plugin is installed and running.' };
            });
    }

    /* ── commands ───────────────────────────────────────────────── */

    /**
     * Send target coordinates to NINA's Framing Assistant.
     * This populates the Framing Assistant with the coordinates, allowing
     * the user to visually frame and then slew.
     *
     * @param {number} raHours - RA in decimal hours (e.g., 5.588 for M42)
     * @param {number} decDeg - Dec in decimal degrees (e.g., -5.391 for M42)
     * @param {string} [objectName] - Object name for display purposes only
     * @returns {Promise<{success: boolean, message: string}>}
     */
    function sendToFramingAssistant(raHours, decDeg, objectName) {
        if (!_baseUrl()) {
            return Promise.resolve({ success: false, message: 'NINA not configured.' });
        }
        if (raHours === null || raHours === undefined || decDeg === null || decDeg === undefined) {
            return Promise.resolve({ success: false, message: 'Missing coordinates.' });
        }

        // Convert RA from hours to degrees (NINA expects decimal degrees)
        var raDeg = parseFloat(raHours) * 15.0;
        var decDegVal = parseFloat(decDeg);

        if (isNaN(raDeg) || isNaN(decDegVal)) {
            return Promise.resolve({ success: false, message: 'Invalid coordinates.' });
        }

        var t = _timeout(5000);
        var url = _baseUrl() + '/framing/set-coordinates?RAangle=' + raDeg + '&DecAngle=' + decDegVal;

        return fetch(url, { signal: t.signal, mode: 'cors' })
            .then(function (r) {
                t.clear();
                return r.json().then(function (data) {
                    if (data.Success) {
                        var msg = objectName 
                            ? 'Sent "' + objectName + '" to NINA Framing Assistant.'
                            : 'Coordinates sent to NINA Framing Assistant.';
                        return { success: true, message: msg };
                    }
                    return { success: false, message: 'NINA error: ' + (data.Error || 'Unknown') };
                });
            })
            .catch(function (err) {
                t.clear();
                if (err.name === 'AbortError') {
                    return { success: false, message: 'Timed out reaching NINA.' };
                }
                // Try no-cors fallback - command may still work
                return _sendNoCors(url, objectName);
            });
    }

    /**
     * Fallback: send command with no-cors mode.
     * The command fires but we can't read the response.
     */
    function _sendNoCors(url, objectName) {
        var t = _timeout(5000);
        return fetch(url, { signal: t.signal, mode: 'no-cors' })
            .then(function () {
                t.clear();
                var msg = objectName
                    ? 'Sent "' + objectName + '" to NINA (response blocked by CORS, but command likely succeeded).'
                    : 'Coordinates sent to NINA (response blocked by CORS, but command likely succeeded).';
                return { success: true, message: msg, corsWarning: true };
            })
            .catch(function (err) {
                t.clear();
                if (err.name === 'AbortError') {
                    return { success: false, message: 'Timed out reaching NINA.' };
                }
                return { success: false, message: 'Cannot reach NINA. Make sure the Advanced API plugin is installed and running.' };
            });
    }

    /**
     * Direct mount slew - bypasses Framing Assistant, slews mount immediately.
     * Use with caution - this moves the telescope!
     *
     * @param {number} raHours - RA in decimal hours
     * @param {number} decDeg - Dec in decimal degrees
     * @param {boolean} [center=false] - Use plate-solve centering
     * @returns {Promise<{success: boolean, message: string}>}
     */
    function slewToCoordinates(raHours, decDeg, center) {
        if (!_baseUrl()) {
            return Promise.resolve({ success: false, message: 'NINA not configured.' });
        }

        var raDeg = parseFloat(raHours) * 15.0;
        var decDegVal = parseFloat(decDeg);

        if (isNaN(raDeg) || isNaN(decDegVal)) {
            return Promise.resolve({ success: false, message: 'Invalid coordinates.' });
        }

        var t = _timeout(10000); // Longer timeout for slew
        var url = _baseUrl() + '/equipment/mount/slew?ra=' + raDeg + '&dec=' + decDegVal 
                + '&center=' + (center ? 'true' : 'false') + '&waitForResult=false';

        return fetch(url, { signal: t.signal, mode: 'cors' })
            .then(function (r) {
                t.clear();
                return r.json().then(function (data) {
                    if (data.Success) {
                        return { success: true, message: 'Slew command sent to NINA.' };
                    }
                    return { success: false, message: 'NINA error: ' + (data.Error || 'Unknown') };
                });
            })
            .catch(function (err) {
                t.clear();
                if (err.name === 'AbortError') {
                    return { success: false, message: 'Timed out reaching NINA.' };
                }
                return { success: false, message: 'Cannot reach NINA.' };
            });
    }

    /* ── UI helpers ─────────────────────────────────────────────── */

    /**
     * Call once on DOMContentLoaded to show / hide every element with
     * class "nova-nina-action" based on the user's NINA settings.
     */
    function initPageActions() {
        return loadSettings().then(function () {
            var els = document.querySelectorAll('.nova-nina-action');
            for (var i = 0; i < els.length; i++) {
                els[i].style.display = isEnabled() ? '' : 'none';
            }
            /* also handle the primary id */
            var btn = document.getElementById('send-to-nina');
            if (btn) btn.style.display = isEnabled() ? '' : 'none';
        });
    }

    /* ── private ────────────────────────────────────────────────── */

    function _copy(o) { return JSON.parse(JSON.stringify(o)); }

    /* ── public API ─────────────────────────────────────────────── */

    window.NovaNINA = {
        loadSettings:          loadSettings,
        getSettings:           getSettings,
        isEnabled:             isEnabled,
        saveSettings:          saveSettings,
        testConnection:        testConnection,
        sendToFramingAssistant: sendToFramingAssistant,
        slewToCoordinates:     slewToCoordinates,
        initPageActions:       initPageActions
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initPageActions);
    } else {
        initPageActions();
    }
})();
