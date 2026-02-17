/* base.js - Global theme, version check, help modal, and centralized state */

// State variables for help modal controller
let helpModalInitialized = false;

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
    closeHelpModal: null
});

// ============================================
// INITIALIZATION
// ============================================

document.addEventListener("DOMContentLoaded", function () {
    // --- INITIALIZE HELP MODAL ---
    // ModalController should be available now (preserved from modal-manager.js)
    if (window.novaState && window.novaState.fn && window.novaState.fn.ModalController) {
        try {
            window.novaState.fn.helpModal = new window.novaState.fn.ModalController('universal-help-modal', {
                contentId: null,
                visibleClass: 'is-visible',
                closeOnBackdrop: true,
                closeOnEscape: true,
                ariaLabelledBy: 'help-modal-title'
            });
            helpModalInitialized = true;
            console.log('[base.js] Help modal controller initialized successfully');
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
                    notificationSpan.innerHTML = ' <span style="font-size: 0.8em; font-weight: normal; color: #83b4c5;">(<a href="' + repo_url + '" target="_blank" style="color: #83b4c5; text-decoration: none;" >Latest version: v' + data.new_version + '</a>)</span>';
                }
            }
        })
        .catch(error => console.error("Update check failed:", error));
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
    body.innerHTML = '<div style="text-align:center; padding: 40px; color: #666;">Loading help content...</div>';

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
                body.innerHTML = '<p style="color:red">Error: Help content returned empty.</p>';
            }
        })
        .catch(err => {
            console.error('[base.js] Error fetching help content:', err);
            body.innerHTML = '<p style="color:red">Network Error: Could not load help topic \'' + topicId + '\'.</p>';
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

// --- Navigation Helper Function ---
function goBack() {
    window.history.back();
}

// Register help functions in novaState
window.novaState.fn.openHelp = openHelp;
window.novaState.fn.closeHelpModal = closeHelpModal;
