/**
 * modal-manager.js - Centralized Modal Management System
 *
 * Provides a unified ModalController class for handling modal dialogs with:
 * - Accessibility (ARIA attributes, focus trap, keyboard navigation)
 * - Backdrop click handling
 * - Escape key handling
 * - Instance caching for complex modals (e.g., Aladin Lite)
 * - Consistent lifecycle callbacks (onOpen, onClose, beforeOpen, beforeClose)
 *
 * Usage:
 *   const modal = new ModalController('modal-id', {
 *     cacheKey: 'framing',  // Optional: cache modal content/instance
 *     onOpen: () => { ... },
 *     onClose: () => { ... }
 *   });
 *   modal.open();
 *   modal.close();
 */

(function() {
    'use strict';

    // Registry for all modal instances
    const modalRegistry = new Map();

    // Track focus for trap
    let focusableSelectors = [
        'a[href]',
        'button:not([disabled])',
        'textarea:not([disabled])',
        'input:not([disabled])',
        'select:not([disabled])',
        '[tabindex]:not([tabindex="-1"])'
    ];
    const focusableSelector = focusableSelectors.join(', ');

    /**
     * ModalController - A class for managing modal dialogs
     *
     * @class ModalController
     * @param {string} modalId - The ID of the modal element
     * @param {Object} options - Configuration options
     * @param {string} options.contentId - ID of the modal content container (defaults to modalId + '-content')
     * @param {string} options.cacheKey - Optional key for caching modal state/instance
     * @param {Function} options.onOpen - Callback when modal opens (after display)
     * @param {Function} options.onClose - Callback when modal closes (after hide)
     * @param {Function} options.beforeOpen - Callback before modal opens (return false to cancel)
     * @param {Function} options.beforeClose - Callback before modal closes (return false to cancel)
     * @param {boolean} options.closeOnBackdrop - Whether to close on backdrop click (default: true)
     * @param {boolean} options.closeOnEscape - Whether to close on Escape key (default: true)
     * @param {string} options.displayStyle - CSS display value when open (default: 'block')
     * @param {string} options.visibleClass - CSS class to add when visible (optional)
     * @param {string} options.ariaLabelledBy - Element ID for aria-labelledby (optional)
     * @param {boolean} options.skipFocus - If true, don't auto-focus any element (default: false)
     */
    class ModalController {
        constructor(modalId, options = {}) {
            this.modalId = modalId;
            this.modalElement = document.getElementById(modalId);

            if (!this.modalElement) {
                console.error(`[ModalController] Modal element with id "${modalId}" not found`);
                return;
            }

            // Content container (for stopPropagation checks)
            this.contentId = options.contentId || `${modalId}-content`;
            this.contentElement = document.getElementById(this.contentId);

            // Configuration
            this.options = {
                closeOnBackdrop: options.closeOnBackdrop !== undefined ? options.closeOnBackdrop : true,
                closeOnEscape: options.closeOnEscape !== undefined ? options.closeOnEscape : true,
                displayStyle: options.displayStyle || 'block',
                visibleClass: options.visibleClass || null,
                ariaLabelledBy: options.ariaLabelledBy || null,
                cacheKey: options.cacheKey || null,
                skipFocus: options.skipFocus || false,
                onOpen: options.onOpen || null,
                onClose: options.onClose || null,
                beforeOpen: options.beforeOpen || null,
                beforeClose: options.beforeClose || null
            };

            // State
            this.isOpen = false;
            this.previousActiveElement = null;
            this.cachedInstance = null;

            // Event handler references (for cleanup)
            this.backdropClickHandler = null;
            this.escapeKeyHandler = null;
            this.focusTrapHandler = null;

            // Initialize ARIA attributes
            this._initAria();

            // Register in global registry
            modalRegistry.set(modalId, this);
        }

        /**
         * Initialize ARIA attributes on the modal
         * @private
         */
        _initAria() {
            // Set role and aria-modal on the modal element
            if (!this.modalElement.getAttribute('role')) {
                this.modalElement.setAttribute('role', 'dialog');
            }
            if (!this.modalElement.getAttribute('aria-modal')) {
                this.modalElement.setAttribute('aria-modal', 'true');
            }

            // Set aria-labelledby if provided
            if (this.options.ariaLabelledBy) {
                this.modalElement.setAttribute('aria-labelledby', this.options.ariaLabelledBy);
            }

            // Set aria-hidden on modal content (will be toggled)
            if (this.contentElement) {
                this.contentElement.setAttribute('aria-hidden', 'false');
            }
        }

        /**
         * Get all focusable elements within the modal
         * @private
         * @returns {NodeList} Focusable elements
         */
        _getFocusableElements() {
            if (!this.contentElement) {
                return this.modalElement.querySelectorAll(focusableSelector);
            }
            return this.contentElement.querySelectorAll(focusableSelector);
        }

        /**
         * Save the currently focused element for restoration later
         * @private
         */
        _saveFocus() {
            this.previousActiveElement = document.activeElement;
        }

        /**
         * Restore focus to the previously focused element
         * @private
         */
        _restoreFocus() {
            if (this.previousActiveElement && this.previousActiveElement.focus) {
                // Use a small timeout to allow other handlers to complete
                setTimeout(() => {
                    this.previousActiveElement.focus();
                }, 10);
            }
        }

        /**
         * Set focus to the first focusable element in the modal
         * @private
         */
        _setInitialFocus() {
            // Always reset scroll position on all potential scroll containers
            this._resetScrollPosition();

            // If skipFocus is enabled, don't focus any element
            if (this.options.skipFocus) {
                return;
            }

            const focusableElements = this._getFocusableElements();
            if (focusableElements.length > 0) {
                // Focus on the first focusable element that is NOT a close button
                // This prevents scrolling to the bottom when close button is focused
                // Convert NodeList to Array for .filter() support
                const focusableArray = Array.from(focusableElements);
                const nonCloseElements = focusableArray.filter(el => {
                    return !el.matches('[data-action*="close"], .close-btn, #close-modal');
                });
                if (nonCloseElements.length > 0 && nonCloseElements[0].focus) {
                    nonCloseElements[0].focus();
                } else if (focusableElements[0].focus) {
                    // Fallback to first focusable element if all are close buttons
                    focusableElements[0].focus();
                }
            }
        }

        /**
         * Reset scroll position on all scrollable containers within the modal
         * @private
         */
        _resetScrollPosition() {
            // Reset main modal element scroll
            this.modalElement.scrollTop = 0;

            // Reset content element scroll if it exists
            if (this.contentElement) {
                this.contentElement.scrollTop = 0;
            }

            // Reset scroll on common scrollable containers within the modal
            const scrollContainers = this.modalElement.querySelectorAll('.insp-modal-body, .modal-content, .help-body, [style*="overflow"]');
            scrollContainers.forEach(el => {
                el.scrollTop = 0;
            });
        }

        /**
         * Handle focus trapping within the modal
         * @private
         * @param {KeyboardEvent} e - The keyboard event
         */
        _handleFocusTrap(e) {
            if (e.key !== 'Tab') return;

            const focusableElements = this._getFocusableElements();
            if (focusableElements.length === 0) return;

            const firstElement = focusableElements[0];
            const lastElement = focusableElements[focusableElements.length - 1];

            if (e.shiftKey) {
                // Shift+Tab: focus the last element if focus is on the first
                if (document.activeElement === firstElement) {
                    e.preventDefault();
                    lastElement.focus();
                }
            } else {
                // Tab: focus the first element if focus is on the last
                if (document.activeElement === lastElement) {
                    e.preventDefault();
                    firstElement.focus();
                }
            }
        }

        /**
         * Handle escape key press
         * @private
         * @param {KeyboardEvent} e - The keyboard event
         */
        _handleEscapeKey(e) {
            if (e.key === 'Escape' || e.key === 'Esc') {
                this.close();
            }
        }

        /**
         * Handle backdrop click
         * @private
         * @param {Event} e - The click event
         */
        _handleBackdropClick(e) {
            // Check if click was on the backdrop (not on content)
            if (e.target === this.modalElement) {
                this.close();
            }
        }

        /**
         * Add event listeners for modal open
         * @private
         */
        _addEventListeners() {
            // Escape key handler
            if (this.options.closeOnEscape) {
                this.escapeKeyHandler = this._handleEscapeKey.bind(this);
                document.addEventListener('keydown', this.escapeKeyHandler);
            }

            // Focus trap handler
            this.focusTrapHandler = this._handleFocusTrap.bind(this);
            document.addEventListener('keydown', this.focusTrapHandler);

            // Backdrop click handler
            if (this.options.closeOnBackdrop) {
                this.backdropClickHandler = this._handleBackdropClick.bind(this);
                this.modalElement.addEventListener('click', this.backdropClickHandler);
            }
        }

        /**
         * Remove event listeners
         * @private
         */
        _removeEventListeners() {
            if (this.escapeKeyHandler) {
                document.removeEventListener('keydown', this.escapeKeyHandler);
                this.escapeKeyHandler = null;
            }
            if (this.focusTrapHandler) {
                document.removeEventListener('keydown', this.focusTrapHandler);
                this.focusTrapHandler = null;
            }
            if (this.backdropClickHandler) {
                this.modalElement.removeEventListener('click', this.backdropClickHandler);
                this.backdropClickHandler = null;
            }
        }

        /**
         * Cache an instance (e.g., Aladin Lite viewer)
         * @param {*} instance - The instance to cache
         */
        setCachedInstance(instance) {
            this.cachedInstance = instance;
        }

        /**
         * Get the cached instance
         * @returns {*} The cached instance or null
         */
        getCachedInstance() {
            return this.cachedInstance;
        }

        /**
         * Clear the cached instance
         */
        clearCache() {
            this.cachedInstance = null;
        }

        /**
         * Open the modal
         * @returns {boolean} Whether the modal was opened
         */
        open() {
            // Check beforeOpen callback
            if (this.options.beforeOpen) {
                try {
                    const shouldOpen = this.options.beforeOpen.call(this);
                    if (shouldOpen === false) {
                        console.log(`[ModalController] Modal "${this.modalId}" open cancelled by beforeOpen callback`);
                        return false;
                    }
                } catch (e) {
                    console.error(`[ModalController] Error in beforeOpen callback:`, e);
                }
            }

            // Don't re-open if already open
            if (this.isOpen) {
                console.warn(`[ModalController] Modal "${this.modalId}" is already open`);
                return false;
            }

            // Save current focus
            this._saveFocus();

            // Show the modal
            this.modalElement.style.display = this.options.displayStyle;

            // Add visible class if specified
            if (this.options.visibleClass) {
                this.modalElement.classList.add(this.options.visibleClass);
            }

            // Mark as open
            this.isOpen = true;

            // Add event listeners
            this._addEventListeners();

            // Set initial focus (small delay to allow browser to render)
            setTimeout(() => {
                this._setInitialFocus();
            }, 50);

            // Call onOpen callback
            if (this.options.onOpen) {
                try {
                    this.options.onOpen.call(this);
                } catch (e) {
                    console.error(`[ModalController] Error in onOpen callback:`, e);
                }
            }

            return true;
        }

        /**
         * Close the modal
         * @returns {boolean} Whether the modal was closed
         */
        close() {
            // Check beforeClose callback
            if (this.options.beforeClose) {
                try {
                    const shouldClose = this.options.beforeClose.call(this);
                    if (shouldClose === false) {
                        console.log(`[ModalController] Modal "${this.modalId}" close cancelled by beforeClose callback`);
                        return false;
                    }
                } catch (e) {
                    console.error(`[ModalController] Error in beforeClose callback:`, e);
                }
            }

            // Don't close if already closed
            if (!this.isOpen) {
                console.warn(`[ModalController] Modal "${this.modalId}" is already closed`);
                return false;
            }

            // Remove event listeners
            this._removeEventListeners();

            // Hide the modal
            this.modalElement.style.display = 'none';

            // Remove visible class if specified
            if (this.options.visibleClass) {
                this.modalElement.classList.remove(this.options.visibleClass);
            }

            // Mark as closed
            this.isOpen = false;

            // Restore focus
            this._restoreFocus();

            // Call onClose callback
            if (this.options.onClose) {
                try {
                    this.options.onClose.call(this);
                } catch (e) {
                    console.error(`[ModalController] Error in onClose callback:`, e);
                }
            }

            return true;
        }

        /**
         * Toggle the modal state
         * @returns {boolean} Whether the modal is now open
         */
        toggle() {
            return this.isOpen ? this.close() : this.open();
        }

        /**
         * Check if the modal is currently open
         * @returns {boolean}
         */
        isOpen() {
            return this.isOpen;
        }

        /**
         * Destroy the modal controller and clean up
         */
        destroy() {
            this.close();
            modalRegistry.delete(this.modalId);
        }
    }

    // Export to window.novaState for global access
    window.novaState = window.novaState || { fn: {} };
    window.novaState.fn.ModalController = ModalController;

    // Convenience function to get a modal by ID
    window.novaState.fn.getModal = function(modalId) {
        return modalRegistry.get(modalId);
    };

    // Export for module systems (if needed)
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = ModalController;
    }

})();
