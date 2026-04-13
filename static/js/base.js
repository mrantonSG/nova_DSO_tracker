/* base.js - Global theme, version check, help modal, about modal, and centralized state */

// State variables for help modal controller
let helpModalInitialized = false;
let aboutModalInitialized = false;

// ============================================
// CENTRALIZED STATE MANAGEMENT
// ============================================

// Initialize novaState object - preserve existing properties (like ModalController)
window.novaState = window.novaState || {};

// State categories - preserve existing data
window.novaState.data = Object.assign(window.novaState.data || {}, {
    // Graph data for object views
    graphData: window.NOVA_GRAPH_DATA || null,

    // Session data
    selectedSessionData: window.selectedSessionData || null,

    // Dashboard data
    latestDSOData: window.latestDSOData || [],
    allSavedViews: window.allSavedViews || {},
    currentFilteredData: window.currentFilteredData || []
});

// Guest/user status - preserve existing flags
window.novaState.flags = Object.assign(window.novaState.flags || {}, {
    isGuestUser: window.IS_GUEST_USER || false,
    isListFiltered: window.isListFiltered || false,
    objectScriptLoaded: false,
    journalSectionInitialized: false
});

// Config data - preserve existing config
window.novaState.config = Object.assign(window.novaState.config || {}, {
    configForm: window.NOVA_CONFIG_FORM || { urls: {}, telemetryEnabled: false },
    indexData: window.NOVA_INDEX || { isGuest: false, hideInvisible: false, altitudeThreshold: 15 }
});

// State functions (exposed globally for backward compatibility)
// IMPORTANT: Preserve existing functions (like ModalController, getModal)
const existingFn = window.novaState.fn || {};
window.novaState.fn = Object.assign(existingFn, {
    // Graph view functions
    showTab: null,
    loadTrixContentEdit: null,
    toggleProjectSubTabEdit: null,
    showProjectSubTab: null,
    changeView: null,
    saveProject: null,
    openFramingAssistant: null,
    closeFramingAssistant: null,
    applyLockToObject: null,
    toggleGeoBelt: null,
    flipFraming90: null,
    copyFramingUrl: null,
    saveFramingToDB: null,
    updateFramingChart: null,
    updateFovVsObjectLabel: null,
    onRotationInput: null,
    setSurvey: null,
    updateImageAdjustments: null,
    copyRaDec: null,
    resetFovCenterToObject: null,
    nudgeFov: null,
    copyAsiairMosaic: null,
    setLocation: null,
    selectSuggestedDate: null,
    openInStellarium: null,

    // Dashboard functions
    openInspirationModal: null,
    showGraph: null,

    // Objects section functions
    filterObjectsList: null,
    selectAllVisibleObjects: null,
    deselectAllObjects: null,
    executeBulkAction: null,
    openDuplicateChecker: null,
    mergeObjects: null,
    activateLazyTrix: null,
    confirmCatalogImport: null,

    // Journal functions
    loadSessionViaAjax: null,

    // Heatmap functions
    updateHeatmapFilter: null,
    fetchAndRenderHeatmap: null,
    resetHeatmapState: null,

    // Help/modal functions
    openHelp: null,
    closeHelpModal: null,

    // About modal functions
    openAboutModal: null,
    closeAboutModal: null
});

// ============================================
// INITIALIZATION
// ============================================

document.addEventListener("DOMContentLoaded", function () {
    // --- INITIALIZE TRANSLATION BANNER ---
    initTranslationBanner();

    // --- INITIALIZE THEME TOGGLE BUTTON ---
    const themeToggleBtn = document.getElementById('theme-toggle');
    if (themeToggleBtn) {
        themeToggleBtn.addEventListener('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            // Use stylingUtils.toggleTheme to switch theme
            if (window.stylingUtils && window.stylingUtils.toggleTheme) {
                window.stylingUtils.toggleTheme();
            } else {
                console.warn('[base.js] stylingUtils.toggleTheme not available');
            }
        });
    }

    // --- INITIALIZE HELP MODAL ---
    // ModalController should be available now (preserved from modal-manager.js)
    if (window.novaState && window.novaState.fn && window.novaState.fn.ModalController) {
        try {
            window.novaState.fn.helpModal = new window.novaState.fn.ModalController('universal-help-modal', {
                contentId: null,
                visibleClass: 'is-visible',
                closeOnBackdrop: true,
                closeOnEscape: true,
                ariaLabelledBy: 'help-modal-title',
                skipFocus: true // Don't auto-focus any element when opening
            });
            helpModalInitialized = true;
        } catch (err) {
            console.error('[base.js] Error initializing help modal:', err);
        }
    } else {
        console.warn('[base.js] ModalController not available, will use DOM fallback');
    }

    // --- EVENT DELEGATION FOR HELP BADGES ---
    // Use a simple event delegation pattern
    document.addEventListener('click', function(e) {
        const target = e.target.closest('.help-badge');
        if (target) {
            e.preventDefault();
            e.stopPropagation();
            const topic = target.getAttribute('data-help-topic');
            if (topic) {
                console.log('[base.js] Help badge clicked for topic:', topic);
                openHelp(topic);
            }
        }
    }, true); // Use capture phase to catch clicks early

    // --- INITIALIZE ABOUT MODAL ---
    if (window.novaState && window.novaState.fn && window.novaState.fn.ModalController) {
        try {
            window.novaState.fn.aboutModal = new window.novaState.fn.ModalController('about-modal', {
                contentId: null,
                displayStyle: 'flex',
                visibleClass: 'is-visible',
                closeOnBackdrop: true,
                closeOnEscape: true,
                ariaLabelledBy: 'about-modal-title',
                skipFocus: true
            });
            aboutModalInitialized = true;
        } catch (err) {
            console.error('[base.js] Error initializing about modal:', err);
        }
    }

    // --- WIRE UP ABOUT TRIGGER BUTTON ---
    const aboutTriggerBtn = document.getElementById('about-trigger');
    if (aboutTriggerBtn) {
        aboutTriggerBtn.addEventListener('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            openAboutModal();
        });
    }

    // --- WIRE UP ABOUT MODAL CLOSE BUTTON ---
    const aboutCloseBtn = document.getElementById('about-modal-close-btn');
    if (aboutCloseBtn) {
        aboutCloseBtn.addEventListener('click', function(e) {
            e.preventDefault();
            closeAboutModal();
        });
    }

    // --- EVENT DELEGATION FOR NAVIGATION BUTTONS ---
    document.addEventListener('click', function(e) {
        if (e.target.dataset.navUrl) {
            window.location.href = e.target.dataset.navUrl;
        }
    });

    // --- VERSION CHECK ---
    fetch('/api/latest_version')
        .then(response => response.json())
        .then(data => {
            if (data && data.new_version) {
                const notificationSpan = document.getElementById('update-notification');
                if (notificationSpan) {
                    const repo_url = data.url || 'https://github.com/YOUR_GITHUB_USERNAME/YOUR_REPO_NAME/releases';
                    const primaryColor = (window.stylingUtils && window.stylingUtils.getPrimaryColor) ? window.stylingUtils.getPrimaryColor() : '#83b4c5';
                    notificationSpan.innerHTML = ' <span style="font-size: 0.8em; font-weight: normal; color: ' + primaryColor + ';">(<a href="' + repo_url + '" target="_blank" style="color: ' + primaryColor + '; text-decoration: none;" >' + window.t('latest_version') + ': v' + data.new_version + '</a>)</span>';
                }
            }
        })
        .catch(error => console.error("Update check failed:", error));

    // --- INITIALIZE TRANSLATION FEEDBACK MODAL ---
    if (window.novaState && window.novaState.fn && window.novaState.fn.ModalController) {
        try {
            window.novaState.fn.translationFeedbackModal = new window.novaState.fn.ModalController('translation-feedback-modal', {
                contentId: null,
                displayStyle: 'flex',
                visibleClass: 'is-visible',
                closeOnBackdrop: true,
                closeOnEscape: true,
                ariaLabelledBy: 'translation-feedback-modal-title',
                skipFocus: true
            });
        } catch (err) {
            console.error('[base.js] Error initializing translation feedback modal:', err);
        }
    }

    // --- WIRE UP TRANSLATION FEEDBACK LINK ---
    const feedbackLink = document.getElementById('translation-feedback-link');
    if (feedbackLink) {
        feedbackLink.addEventListener('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            openTranslationFeedbackModal();
        });
    }

    // --- WIRE UP TRANSLATION FEEDBACK FORM ---
    const feedbackForm = document.getElementById('translation-feedback-form');
    if (feedbackForm) {
        feedbackForm.addEventListener('submit', handleTranslationFeedbackSubmit);
    }

    // --- WIRE UP TRANSLATION FEEDBACK CANCEL BUTTON ---
    const feedbackCancelBtn = document.getElementById('feedback-cancel-btn');
    if (feedbackCancelBtn) {
        feedbackCancelBtn.addEventListener('click', closeTranslationFeedbackModal);
    }

    // --- WIRE UP TRANSLATION FEEDBACK CLOSE BUTTON ---
    const feedbackCloseBtn = document.getElementById('translation-feedback-modal-close-btn');
    if (feedbackCloseBtn) {
        feedbackCloseBtn.addEventListener('click', closeTranslationFeedbackModal);
    }
});

// --- HELP MODAL FUNCTIONS ---
function openHelp(topicId) {
    console.log('[base.js] openHelp called with topic:', topicId);

    const body = document.getElementById('help-modal-body');
    const modalElement = document.getElementById('universal-help-modal');
    const closeBtn = document.getElementById('help-modal-close-btn');

    if (!body || !modalElement) {
        console.error('[base.js] Help modal elements not found');
        return;
    }

    // Reset and show loading state
    body.innerHTML = '<div style="text-align:center; padding: 40px; color: ' + ((window.stylingUtils && window.stylingUtils.getColor) ? window.stylingUtils.getColor('--text-secondary', '#666') : '#666') + ';">' + window.t('loading_help') + '</div>';

    // Try to use ModalController if available
    const controller = window.novaState.fn.helpModal;
    const usingFallback = !controller;
    if (controller) {
        console.log('[base.js] Using ModalController');
        controller.open();
    } else {
        console.log('[base.js] Using DOM fallback');
        // Fallback: Just add the class and let CSS handle display and centering
        modalElement.classList.add('is-visible');
    }

    // Wire up close button (do this once, not on every open)
    if (closeBtn && !closeBtn.dataset.helpCloseWired) {
        closeBtn.addEventListener('click', () => {
            if (window.novaState.fn.helpModal) {
                window.novaState.fn.helpModal.close();
            } else {
                modalElement.classList.remove('is-visible');
            }
        });
        closeBtn.dataset.helpCloseWired = 'true';
    }

    // Wire up close-X button
    const closeX = document.getElementById('help-modal-close-x');
    if (closeX && !closeX.dataset.helpCloseWired) {
        closeX.addEventListener('click', () => {
            if (window.novaState.fn.helpModal) {
                window.novaState.fn.helpModal.close();
            } else {
                modalElement.classList.remove('is-visible');
            }
        });
        closeX.dataset.helpCloseWired = 'true';
    }

    // Fetch content
    fetch('/api/help/' + topicId)
        .then(response => {
            if (!response.ok) throw new Error('Network response was not ok');
            return response.json();
        })
        .then(data => {
            if (data.html) {
                body.innerHTML = data.html;
            } else {
                body.innerHTML = '<p style="color:red">' + window.t('help_content_empty') + '</p>';
            }
        })
        .catch(err => {
            console.error('[base.js] Error fetching help content:', err);
            body.innerHTML = `<p style="color:red">${window.t('help_network_error')} '${topicId}'.</p>`;
        });
}

function closeHelpModal() {
    const controller = window.novaState.fn.helpModal;

    if (controller) {
        controller.close();
    } else {
        const modalElement = document.getElementById('universal-help-modal');
        if (modalElement) {
            modalElement.style.display = 'none';
            modalElement.classList.remove('is-visible');
        }
    }
}

// --- ABOUT MODAL FUNCTIONS ---
function openAboutModal() {
    console.log('[base.js] openAboutModal called');
    const modalElement = document.getElementById('about-modal');

    if (!modalElement) {
        console.error('[base.js] About modal element not found');
        return;
    }

    const controller = window.novaState.fn.aboutModal;
    if (controller) {
        console.log('[base.js] Using ModalController for about modal');
        controller.open();
    } else {
        console.log('[base.js] Using DOM fallback for about modal');
        modalElement.style.display = 'flex';
        modalElement.classList.add('is-visible');
    }
}

function closeAboutModal() {
    const controller = window.novaState.fn.aboutModal;

    if (controller) {
        controller.close();
    } else {
        const modalElement = document.getElementById('about-modal');
        if (modalElement) {
            modalElement.style.display = 'none';
            modalElement.classList.remove('is-visible');
        }
    }
}

// --- Navigation Helper Function ---
function goBack() {
    window.history.back();
}

// Register help functions in novaState
window.novaState.fn.openHelp = openHelp;
window.novaState.fn.closeHelpModal = closeHelpModal;

// Register about modal functions in novaState
window.novaState.fn.openAboutModal = openAboutModal;
window.novaState.fn.closeAboutModal = closeAboutModal;

// --- TRANSLATION FEEDBACK MODAL FUNCTIONS ---
function openTranslationFeedbackModal() {
    const modalElement = document.getElementById('translation-feedback-modal');

    if (!modalElement) {
        console.error('[base.js] Translation feedback modal element not found');
        return;
    }

    // Pre-fill the locale field with current language
    const currentLang = window.NOVA_CONFIG && window.NOVA_CONFIG.language ? window.NOVA_CONFIG.language : 'en';
    const localeInput = document.getElementById('feedback-locale');
    if (localeInput) {
        localeInput.value = currentLang;
    }

    // Clear form fields except locale
    document.getElementById('feedback-term').value = '';
    document.getElementById('feedback-suggestion').value = '';
    document.getElementById('feedback-notes').value = '';

    const controller = window.novaState.fn.translationFeedbackModal;
    if (controller) {
        console.log('[base.js] Using ModalController for translation feedback modal');
        controller.open();
    } else {
        console.log('[base.js] Using DOM fallback for translation feedback modal');
        modalElement.style.display = 'flex';
        modalElement.classList.add('is-visible');
    }
}

function closeTranslationFeedbackModal() {
    const controller = window.novaState.fn.translationFeedbackModal;

    if (controller) {
        controller.close();
    } else {
        const modalElement = document.getElementById('translation-feedback-modal');
        if (modalElement) {
            modalElement.style.display = 'none';
            modalElement.classList.remove('is-visible');
        }
    }
}

function handleTranslationFeedbackSubmit(e) {
    e.preventDefault();

    const termInput = document.getElementById('feedback-term');
    const suggestionInput = document.getElementById('feedback-suggestion');
    const notesInput = document.getElementById('feedback-notes');
    const localeInput = document.getElementById('feedback-locale');

    // Get values
    const locale = localeInput.value.trim();
    const term = termInput.value.trim();
    const suggestion = suggestionInput.value.trim();
    const notes = notesInput.value.trim();

    // Basic client-side validation
    if (!term) {
        showFeedbackError('Please enter the term or phrase that seems wrong.');
        termInput.focus();
        return;
    }

    if (!suggestion) {
        showFeedbackError('Please provide a suggested correction.');
        suggestionInput.focus();
        return;
    }

    // Build GitHub Issue URL
    const base = "https://github.com/mrantonSG/nova_DSO_tracker/issues/new";
    const title = `[Translation Feedback] ${locale} — ${term}`;
    const body = `**Locale:** ${locale}\n**Term / Phrase:** ${term}\n**Suggested Correction:** ${suggestion}\n**Context / Notes:** ${notes}`;
    const url = `${base}?title=${encodeURIComponent(title)}&body=${encodeURIComponent(body)}`;

    // Open in new tab
    window.open(url, '_blank');

    // Close the modal after opening the tab
    closeTranslationFeedbackModal();
}

// --- TRANSLATION BANNER FUNCTIONS ---
function initTranslationBanner() {
    const banner = document.getElementById('translation-banner');
    if (!banner) return;

    // Get current language from Nova config
    const currentLang = window.NOVA_CONFIG && window.NOVA_CONFIG.language ? window.NOVA_CONFIG.language : 'en';
    const sessionStorageKey = 'banner_dismissed_' + currentLang;

    // Check if banner was previously dismissed for this language
    const isDismissed = sessionStorage.getItem(sessionStorageKey);

    // Show banner only if not dismissed and language has 'auto' status
    if (!isDismissed && banner.dataset.translationStatus === 'auto') {
        banner.style.display = 'flex';
    }

    // Wire up dismiss button
    const dismissBtn = banner.querySelector('.translation-banner-close');
    if (dismissBtn) {
        dismissBtn.addEventListener('click', function() {
            // Hide banner
            banner.style.display = 'none';
            // Store dismissal in sessionStorage
            try {
                sessionStorage.setItem(sessionStorageKey, 'true');
            } catch (e) {
                console.warn('[base.js] sessionStorage not available:', e);
            }
        });
    }
}

// Clear all translation banner dismiss flags when switching languages
// This is called before page reload via language selector
function clearTranslationBannerFlags() {
    try {
        // Get all sessionStorage keys that start with 'banner_dismissed_'
        const keys = [];
        for (let i = 0; i < sessionStorage.length; i++) {
            const key = sessionStorage.key(i);
            if (key && key.startsWith('banner_dismissed_')) {
                keys.push(key);
            }
        }
        // Remove all banner dismiss flags
        keys.forEach(function(key) {
            sessionStorage.removeItem(key);
        });
    } catch (e) {
        console.warn('[base.js] sessionStorage not available:', e);
    }
}

// Hook into language selector to clear flags on language change
document.addEventListener('change', function(e) {
    if (e.target && e.target.id === 'language-select') {
        clearTranslationBannerFlags();
    }
});;
