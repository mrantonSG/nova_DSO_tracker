/* base.js - Global theme, version check, help modal, and centralized state */

// ============================================
// CENTRALIZED STATE MANAGEMENT
// ============================================

// Initialize novaState object
window.novaState = window.novaState || {};

// State categories
window.novaState.data = {
    // Graph data for object views
    graphData: window.NOVA_GRAPH_DATA || null,

    // Session data
    selectedSessionData: window.selectedSessionData || null,

    // Dashboard data
    latestDSOData: window.latestDSOData || [],
    allSavedViews: window.allSavedViews || {},
    currentFilteredData: window.currentFilteredData || []
};

// Guest/user status
window.novaState.flags = {
    isGuestUser: window.IS_GUEST_USER || false,
    isListFiltered: window.isListFiltered || false,
    objectScriptLoaded: false,
    journalSectionInitialized: false
};

// Config data
window.novaState.config = {
    configForm: window.NOVA_CONFIG_FORM || { urls: {}, telemetryEnabled: false },
    indexData: window.NOVA_INDEX || { isGuest: false, hideInvisible: false, altitudeThreshold: 15 }
};

// State functions (exposed globally for backward compatibility)
window.novaState.fn = {
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
};

// ============================================
// THEME & VERSION INITIALIZATION
// ============================================

document.addEventListener("DOMContentLoaded", function () {
    // --- THEME INITIALIZATION ---
    const themeBtn = document.getElementById('theme-toggle-btn');
    const redModeBtn = document.getElementById('red-mode-btn');
    const html = document.documentElement;

    // 1. Check Dark Mode
    const savedTheme = localStorage.getItem('theme');
    if (savedTheme === 'dark-mode') {
        html.classList.add('dark-mode');
        if (themeBtn) {
            themeBtn.textContent = 'Day View';
        }
    } else {
        if (themeBtn) {
            themeBtn.textContent = 'Night View';
        }
    }

    // 2. Check Red Mode
    const savedRedMode = localStorage.getItem('red-mode');
    if (savedRedMode === 'true') {
        html.classList.add('red-mode');
    }

    // --- EVENT LISTENERS FOR THEME BUTTONS ---
    if (themeBtn) {
        themeBtn.addEventListener('click', toggleTheme);
    }
    if (redModeBtn) {
        redModeBtn.addEventListener('click', toggleRedMode);
    }

    // --- EVENT LISTENERS FOR HELP MODAL ---
    const helpModal = document.getElementById('universal-help-modal');
    const helpModalCloseBtn = document.getElementById('help-modal-close-btn');

    if (helpModal) {
        helpModal.addEventListener('click', function(e) {
            if (e.target === helpModal) {
                closeHelpModal();
            }
        });
    }
    if (helpModalCloseBtn) {
        helpModalCloseBtn.addEventListener('click', closeHelpModal);
    }

    // --- EVENT DELEGATION FOR HELP BADGES ---
    document.addEventListener('click', function(e) {
        if (e.target.classList.contains('help-badge')) {
            const topic = e.target.dataset.helpTopic || e.target.getAttribute('data-help-topic');
            if (topic) {
                openHelp(topic);
            }
        }
    });

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

// --- THEME TOGGLE FUNCTIONS ---
function toggleTheme() {
    const html = document.documentElement;
    const themeBtn = document.getElementById('theme-toggle-btn');

    html.classList.toggle('dark-mode');

    if (html.classList.contains('dark-mode')) {
        localStorage.setItem('theme', 'dark-mode');
        if (themeBtn) {
            themeBtn.textContent = 'Day View';
        }
    } else {
        localStorage.setItem('theme', '');
        if (themeBtn) {
            themeBtn.textContent = 'Night View';
        }
    }
    // Force charts to redraw if they exist
    window.dispatchEvent(new Event('resize'));
}

function toggleRedMode() {
    const html = document.documentElement;
    html.classList.toggle('red-mode');

    if (html.classList.contains('red-mode')) {
        localStorage.setItem('red-mode', 'true');
        // Ensure Dark Mode is ON when Red Mode is activated for best effect
        if (!html.classList.contains('dark-mode')) {
            toggleTheme();
        }
    } else {
        localStorage.setItem('red-mode', 'false');
    }
}

// --- HELP MODAL FUNCTIONS ---
function openHelp(topicId) {
    const modal = document.getElementById('universal-help-modal');
    const body = document.getElementById('help-modal-body');

    // 1. Reset and show loading state
    body.innerHTML = '<div style="text-align:center; padding: 40px; color: #666;">Loading help content...</div>';
    modal.classList.add('is-visible');

    // 2. Fetch content
    fetch('/api/help/' + topicId)
        .then(response => response.json())
        .then(data => {
            if (data.html) {
                body.innerHTML = data.html;
            } else {
                body.innerHTML = '<p style="color:red">Error: Help content returned empty.</p>';
            }
        })
        .catch(err => {
            console.error(err);
            body.innerHTML = '<p style="color:red">Network Error: Could not load help topic \'' + topicId + '\'.</p>';
        });
}

function closeHelpModal() {
    document.getElementById('universal-help-modal').classList.remove('is-visible');
}

// --- Navigation Helper Function ---
function goBack() {
    window.history.back();
}

// Register help functions in novaState
window.novaState.fn.openHelp = openHelp;
window.novaState.fn.closeHelpModal = closeHelpModal;
