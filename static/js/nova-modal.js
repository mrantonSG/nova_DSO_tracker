/**
 * nova-modal.js - Reusable confirm/alert modal to replace native confirm()/alert()
 *
 * Exposes:
 *   window.novaConfirm(message, options) -> Promise<boolean>
 *   window.novaAlert(message, options)   -> Promise<void>
 *
 * options: { title, confirmLabel, cancelLabel, danger }
 *
 * Builds one reusable modal DOM node and reuses it across calls. Delegates
 * escape-key close, backdrop-click close, focus-trap, and focus-restore to
 * window.novaState.fn.ModalController (static/js/modal-manager.js), which is
 * already loaded globally in base.html and used by the app's other modals
 * (#about-modal, #universal-help-modal, #translation-feedback-modal).
 *
 * Phase 1: nothing calls this yet. Button labels are hardcoded English —
 * TODO: needs i18n in a later step.
 */
(function () {
    'use strict';

    var els = null;
    var controller = null;

    var STATE = {
        mode: null,       // 'confirm' | 'alert'
        resolve: null,
        resolved: false
    };

    function buildModal() {
        if (els) return els;

        var overlay = document.createElement('div');
        overlay.id = 'nova-modal';
        overlay.setAttribute('role', 'alertdialog');
        overlay.setAttribute('aria-labelledby', 'nova-modal-title');
        overlay.setAttribute('aria-describedby', 'nova-modal-message');

        overlay.innerHTML =
            '<div class="modal-content nova-modal-content" id="nova-modal-content">' +
                '<h2 class="nova-modal-title" id="nova-modal-title"></h2>' +
                '<p class="nova-modal-message" id="nova-modal-message"></p>' +
                '<div class="nova-modal-buttons">' +
                    '<button type="button" class="btn-secondary nova-modal-cancel" id="nova-modal-cancel"></button>' +
                    '<button type="button" class="action-button nova-modal-confirm" id="nova-modal-confirm"></button>' +
                '</div>' +
            '</div>';

        document.body.appendChild(overlay);

        els = {
            overlay: overlay,
            title: overlay.querySelector('#nova-modal-title'),
            message: overlay.querySelector('#nova-modal-message'),
            cancelBtn: overlay.querySelector('#nova-modal-cancel'),
            confirmBtn: overlay.querySelector('#nova-modal-confirm')
        };

        return els;
    }

    function getController() {
        buildModal();
        if (controller) return controller;

        if (!window.novaState || !window.novaState.fn || !window.novaState.fn.ModalController) {
            console.error('[nova-modal] window.novaState.fn.ModalController is not available. ' +
                'Make sure modal-manager.js loads before nova-modal.js.');
            return null;
        }

        controller = new window.novaState.fn.ModalController('nova-modal', {
            contentId: 'nova-modal-content',
            closeOnBackdrop: true,
            closeOnEscape: true,
            displayStyle: 'flex',
            visibleClass: 'is-visible',
            skipFocus: true, // we focus the confirm button ourselves
            onClose: handleControllerClose
        });

        return controller;
    }

    // Fires on every close() (button click, backdrop click, Escape).
    // Button handlers already resolve + mark STATE.resolved before calling
    // close(), so this only settles the promise for backdrop/Escape dismissal.
    function handleControllerClose() {
        settle(STATE.mode === 'confirm' ? false : undefined);
    }

    function settle(value) {
        if (STATE.resolved) return;
        STATE.resolved = true;
        var resolve = STATE.resolve;
        STATE.resolve = null;
        if (resolve) resolve(value);
    }

    function closeWith(value) {
        settle(value);
        var ctrl = getController();
        if (ctrl) ctrl.close();
    }

    // If a previous novaConfirm/novaAlert call is still pending when a new
    // one starts, resolve it as dismissed so no caller is left hanging.
    function preemptPending() {
        if (STATE.resolve && !STATE.resolved) {
            settle(STATE.mode === 'confirm' ? false : undefined);
        }
    }

    function configure(mode, message, options) {
        var title = options.title;
        if (title) {
            els.title.textContent = title;
            els.title.style.display = '';
        } else {
            els.title.textContent = '';
            els.title.style.display = 'none';
        }

        els.message.textContent = message == null ? '' : String(message);

        var defaultConfirmLabel = mode === 'alert' ? 'OK' : 'Confirm'; // TODO: needs i18n in a later step
        els.confirmBtn.textContent = options.confirmLabel || defaultConfirmLabel;
        els.cancelBtn.textContent = options.cancelLabel || 'Cancel'; // TODO: needs i18n in a later step

        els.confirmBtn.className = 'nova-modal-confirm ' + (options.danger ? 'btn-danger' : 'action-button');
        els.cancelBtn.style.display = mode === 'alert' ? 'none' : '';
        els.overlay.classList.toggle('nova-modal-alert', mode === 'alert');
    }

    function focusConfirmSoon() {
        setTimeout(function () {
            if (els.confirmBtn && typeof els.confirmBtn.focus === 'function') {
                els.confirmBtn.focus();
            }
        }, 60);
    }

    function openModal(mode, message, options, resolve) {
        var ctrl = getController();
        if (!ctrl) {
            // ModalController missing is a load-order bug, not a runtime
            // scenario the caller can recover from.
            resolve(mode === 'confirm' ? false : undefined);
            return;
        }

        preemptPending();
        configure(mode, message, options);

        STATE.mode = mode;
        STATE.resolve = resolve;
        STATE.resolved = false;

        els.cancelBtn.onclick = function () { closeWith(false); };
        els.confirmBtn.onclick = function () { closeWith(mode === 'confirm' ? true : undefined); };

        ctrl.open();
        focusConfirmSoon();
    }

    window.novaConfirm = function (message, options) {
        options = options || {};
        return new Promise(function (resolve) {
            openModal('confirm', message, options, resolve);
        });
    };

    window.novaAlert = function (message, options) {
        options = options || {};
        return new Promise(function (resolve) {
            openModal('alert', message, options, resolve);
        });
    };

    // --- Event delegation for form confirmations ---
    document.addEventListener('submit', function (e) {
        var form = e.target;
        if (!form.dataset || !form.dataset.confirm) return;
        if (form.dataset.confirmBypass === 'true') {
            delete form.dataset.confirmBypass;
            return;
        }
        e.preventDefault();
        window.novaConfirm(form.dataset.confirm).then(function (confirmed) {
            if (confirmed) {
                form.dataset.confirmBypass = 'true';
                form.requestSubmit();
            }
        });
    });

})();
