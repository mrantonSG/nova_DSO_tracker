/* desktop-connector.js - Behavior specific to Nova running inside the nova_desktop Tauri app.
   Self-contained and additive: safe to delete without affecting normal browser use. */

// Lookup of query param -> action, so new triggers can be added easily.
const NOVA_DESKTOP_TRIGGERS = {
    show_about: function () {
        if (window.novaState && window.novaState.fn && typeof window.novaState.fn.openAboutModal === 'function') {
            window.novaState.fn.openAboutModal();
        }
    }
};

(function runNovaDesktopTriggers() {
    const params = new URLSearchParams(window.location.search);
    Object.keys(NOVA_DESKTOP_TRIGGERS).forEach(function (param) {
        if (params.get(param) === '1') {
            NOVA_DESKTOP_TRIGGERS[param]();
        }
    });
})();
