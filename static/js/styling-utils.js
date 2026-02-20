/**
 * styling-utils.js - CSS Variable Utility Functions
 *
 * Provides utility functions to retrieve CSS variable values from the DOM
 * with safe fallback defaults if variables are not defined.
 *
 * @module styling-utils
 */

'use strict';

// ==========================================================================
// STYLING CONSTANTS
// ==========================================================================

// Fallback color constants (used if CSS variables are not defined)
// These are now theme-aware and update when theme changes
window.stylingUtils_FALLBACKS = {
    // Light theme fallbacks (default)
    light: {
        // Primary brand colors
        PRIMARY_COLOR: '#83b4c5',
        PRIMARY_DARK: '#6795a4',

        // Chart colors
        CHART_LINE_BLUE: '#36A2EB',
        CHART_LINE_YELLOW: '#FFC107',
        CHART_LINE_GRAY: '#636e72',
        CHART_LINE_MAGENTA: '#FF00FF',
        CHART_LINE_PRIMARY: '#83b4c5',
        CHART_TEXT_DARK: '#333',
        CHART_TEXT_LIGHT: '#FFFFFF',

        // Semantic colors
        SUCCESS_COLOR: '#28a745',
        WARNING_COLOR: '#ffc107',
        DANGER_COLOR: '#dc3545',
        DANGER_COLOR_DARK: '#c0392b',

        // Background colors
        BG_WHITE: '#ffffff',
        BG_LIGHT: '#f8f9fa',
        BG_MEDIUM: '#e9e9e9',
        BG_LIGHT_GRAY: '#f0f0f0',

        // Text colors
        TEXT_PRIMARY: '#333',
        TEXT_SECONDARY: '#555',
        TEXT_MUTED: '#888',
        TEXT_MUTED_ALT: '#999',

        // Border colors
        BORDER_LIGHT: '#eee',
        BORDER_MEDIUM: '#ddd',
        BORDER_DARK: '#555',

        // Special colors
        INFO_COLOR_ALT2: '#007bff',
        FOV_COLOR: '#83b4c5',
        SIMULATION_ACTIVE: '#ca0e0e',
        SIMULATION_ERROR_RED: '#f8d7da',
        SIMULATION_ERROR_TEXT: '#721c24',
        GEOSTATIONARY_COLOR: '#e056fd',
        SAMPLING_GOOD: '#2ecc71',
        SAMPLING_OVERSAMPLED: '#9b59b6',
        SAMPLING_SLIGHTLY_OVERSAMPLED: '#3498db',

        // Weather overlay colors (cloud and seeing conditions)
        WEATHER_CLOUD_1: 'rgba(135, 206, 250, 0.15)',  // Clear
        WEATHER_CLOUD_2: 'rgba(135, 206, 250, 0.25)',  // P. Clear
        WEATHER_CLOUD_3: 'rgba(170, 170, 170, 0.2)',   // P. Clear
        WEATHER_CLOUD_4: 'rgba(170, 170, 170, 0.3)',   // P. Clear
        WEATHER_CLOUD_5: 'rgba(120, 120, 120, 0.35)',  // P. Cloudy
        WEATHER_CLOUD_6: 'rgba(120, 120, 120, 0.45)',  // P. Cloudy
        WEATHER_CLOUD_7: 'rgba(80, 80, 80, 0.5)',      // Cloudy
        WEATHER_CLOUD_8: 'rgba(80, 80, 80, 0.6)',      // Cloudy
        WEATHER_CLOUD_9: 'rgba(50, 50, 50, 0.7)',      // Overcast

        WEATHER_SEEING_1: 'rgba(0, 255, 127, 0.2)',   // Excellent
        WEATHER_SEEING_2: 'rgba(0, 255, 127, 0.3)',   // Good
        WEATHER_SEEING_3: 'rgba(173, 255, 47, 0.3)',   // Good
        WEATHER_SEEING_4: 'rgba(255, 255, 0, 0.3)',     // Average
        WEATHER_SEEING_5: 'rgba(255, 215, 0, 0.3)',     // Average
        WEATHER_SEEING_6: 'rgba(255, 165, 0, 0.3)',     // Poor
        WEATHER_SEEING_7: 'rgba(255, 69, 0, 0.3)',      // Poor
        WEATHER_SEEING_8: 'rgba(255, 0, 0, 0.3)'        // Bad
    },

    // Dark theme fallbacks
    dark: {
        // Primary brand colors (unchanged)
        PRIMARY_COLOR: '#83b4c5',
        PRIMARY_DARK: '#6795a4',

        // Chart colors (high contrast for dark mode)
        CHART_LINE_BLUE: '#4da6ff',
        CHART_LINE_YELLOW: '#ffb74d',
        CHART_LINE_GRAY: '#9e9e9e',
        CHART_LINE_MAGENTA: '#ff40ff',
        CHART_LINE_PRIMARY: '#83b4c5',
        CHART_TEXT_DARK: '#e0e0e0',
        CHART_TEXT_LIGHT: '#ffffff',

        // Semantic colors
        SUCCESS_COLOR: '#4caf50',
        WARNING_COLOR: '#ffc107',
        DANGER_COLOR: '#f44336',
        DANGER_COLOR_DARK: '#d32f2f',

        // Background colors
        BG_WHITE: '#121212',
        BG_LIGHT: '#1e1e1e',
        BG_MEDIUM: '#3a3a3a',
        BG_LIGHT_GRAY: '#2c2c2c',

        // Text colors
        TEXT_PRIMARY: '#e0e0e0',
        TEXT_SECONDARY: '#b0b0b0',
        TEXT_MUTED: '#606060',
        TEXT_MUTED_ALT: '#505050',

        // Border colors
        BORDER_LIGHT: '#444444',
        BORDER_MEDIUM: '#333333',
        BORDER_DARK: '#2a2a2a',

        // Special colors
        INFO_COLOR_ALT2: '#4da6ff',
        FOV_COLOR: '#83b4c5',
        SIMULATION_ACTIVE: '#e53935',
        SIMULATION_ERROR_RED: '#3a1a1f',
        SIMULATION_ERROR_TEXT: '#f44336',
        GEOSTATIONARY_COLOR: '#e056fd',
        SAMPLING_GOOD: '#2ecc71',
        SAMPLING_OVERSAMPLED: '#9b59b6',
        SAMPLING_SLIGHTLY_OVERSAMPLED: '#3498db',

        // Weather overlay colors (cloud and seeing conditions - darker for dark mode)
        WEATHER_CLOUD_1: 'rgba(100, 150, 200, 0.15)',   // Clear
        WEATHER_CLOUD_2: 'rgba(100, 150, 200, 0.20)',   // P. Clear
        WEATHER_CLOUD_3: 'rgba(120, 120, 120, 0.18)',   // P. Clear
        WEATHER_CLOUD_4: 'rgba(120, 120, 120, 0.22)',   // P. Clear
        WEATHER_CLOUD_5: 'rgba(90, 90, 90, 0.25)',     // P. Cloudy
        WEATHER_CLOUD_6: 'rgba(90, 90, 90, 0.30)',     // P. Cloudy
        WEATHER_CLOUD_7: 'rgba(60, 60, 60, 0.35)',      // Cloudy
        WEATHER_CLOUD_8: 'rgba(60, 60, 60, 0.40)',      // Cloudy
        WEATHER_CLOUD_9: 'rgba(40, 40, 40, 0.45)',      // Overcast

        WEATHER_SEEING_1: 'rgba(0, 255, 127, 0.25)',    // Excellent
        WEATHER_SEEING_2: 'rgba(0, 255, 127, 0.30)',    // Good
        WEATHER_SEEING_3: 'rgba(173, 255, 47, 0.30)',    // Good
        WEATHER_SEEING_4: 'rgba(255, 255, 0, 0.30)',      // Average
        WEATHER_SEEING_5: 'rgba(255, 215, 0, 0.30)',      // Average
        WEATHER_SEEING_6: 'rgba(255, 165, 0, 0.30)',      // Poor
        WEATHER_SEEING_7: 'rgba(255, 69, 0, 0.30)',       // Poor
        WEATHER_SEEING_8: 'rgba(255, 0, 0, 0.30)'         // Bad
    }
};

/**
 * Get theme-specific fallback value
 * @param {string} key - The fallback key to retrieve
 * @returns {string} The theme-specific fallback value
 */
window.stylingUtils_getThemeFallback = function(key) {
    var currentTheme = window.stylingUtils_isDarkTheme() ? 'dark' : 'light';
    var fallbacks = window.stylingUtils_FALLBACKS[currentTheme];
    return fallbacks[key] !== undefined ? fallbacks[key] : '';
};

/**
 * Register a callback for theme changes
 * @param {Function} callback - Function to call when theme changes
 * @returns {Function} Unsubscribe function
 */
window.stylingUtils_onThemeChange = function(callback) {
    if (typeof callback !== 'function') {
        console.warn('[styling-utils] onThemeChange: callback must be a function');
        return function() {};
    }

    // Add event listener
    window.addEventListener('themeChanged', callback);

    // Return unsubscribe function
    return function() {
        window.removeEventListener('themeChanged', callback);
    };
};

// ==========================================================================
// CSS VARIABLE RETRIEVAL
// ==========================================================================

/**
 * Get a CSS custom property value from the computed style
 * @param {string} varName - The CSS variable name (e.g., '--primary-color')
 * @param {string} fallback - Fallback value if variable is not defined
 * @param {Element} element - The element to get computed style from (default: document.documentElement)
 * @returns {string} The computed value or fallback
 */
window.stylingUtils_getCssVar = function(varName, fallback = '', element = null) {
    try {
        const targetElement = element || document.documentElement;
        const computedStyle = window.getComputedStyle(targetElement);
        const value = computedStyle.getPropertyValue(varName);

        // Return the value if it's defined and not empty, otherwise use fallback
        if (value && value.trim() !== '') {
            return value.trim();
        }
        return fallback;
    } catch (e) {
        console.warn(`[styling-utils] Failed to get CSS variable ${varName}:`, e);
        return fallback;
    }
};

/**
 * Get a CSS custom property and convert to RGB
 * Useful for color manipulation in charts
 * @param {string} varName - The CSS variable name (e.g., '--primary-color')
 * @param {string} fallback - Fallback color in hex format
 * @param {Element} element - The element to get computed style from
 * @returns {string|null} RGB format or null if conversion fails
 */
window.stylingUtils_getCssVarAsRgb = function(varName, fallback = '#000000', element = null) {
    try {
        const hexValue = window.stylingUtils_getCssVar(varName, fallback, element);
        // If it's already RGB or RGBA, return as-is
        if (hexValue.startsWith('rgb')) {
            return hexValue;
        }
        // Convert hex to RGB
        const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hexValue);
        if (!result) {
            return null;
        }
        const r = parseInt(result[1], 16);
        const g = parseInt(result[2], 16);
        const b = parseInt(result[3], 16);
        return `rgb(${r}, ${g}, ${b})`;
    } catch (e) {
        console.warn(`[styling-utils] Failed to convert ${varName} to RGB:`, e);
        return null;
    }
};

/**
 * Get a CSS custom property and convert to RGBA with opacity
 * @param {string} varName - The CSS variable name (e.g., '--primary-color')
 * @param {string} fallback - Fallback color in hex format
 * @param {number} opacity - Opacity value (0-1)
 * @param {Element} element - The element to get computed style from
 * @returns {string|null} RGBA format or null if conversion fails
 */
window.stylingUtils_getCssVarAsRgba = function(varName, fallback = '#000000', opacity = 1, element = null) {
    try {
        const hexValue = window.stylingUtils_getCssVar(varName, fallback, element);
        // If it's already RGBA, return as-is with updated opacity
        if (hexValue.startsWith('rgba')) {
            const parts = hexValue.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)/i);
            if (parts) {
                return `rgba(${parts[1]}, ${parts[2]}, ${parts[3]}, ${opacity})`;
            }
            return hexValue;
        }
        // Convert hex to RGBA
        const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hexValue);
        if (!result) {
            return null;
        }
        const r = parseInt(result[1], 16);
        const g = parseInt(result[2], 16);
        const b = parseInt(result[3], 16);
        return `rgba(${r}, ${g}, ${b}, ${opacity})`;
    } catch (e) {
        console.warn(`[styling-utils] Failed to convert ${varName} to RGBA:`, e);
        return null;
    }
};

// ==========================================================================
// COLOR MAPPING CONSTANTS
// ==========================================================================

/**
 * Color mapping for common colors to CSS variable names
 * Maps semantic names to CSS variable names for easy lookup
 */
window.stylingUtils_COLOR_VARS = {
    // Primary brand
    PRIMARY: '--primary-color',
    PRIMARY_DARK: '--primary-dark',

    // Chart colors
    CHART_LINE_1: '--chart-line-1',
    CHART_LINE_2: '--chart-line-2',
    CHART_LINE_3: '--chart-line-3',
    CHART_LINE_4: '--chart-line-4',
    CHART_LINE_PRIMARY: '--primary-color',
    CHART_TEXT_DARK: '--text-primary',
    CHART_TEXT_LIGHT: '--text-white',

    // Semantic
    SUCCESS: '--success-color',
    WARNING: '--warning-color',
    DANGER: '--danger-color',
    DANGER_DARK: '--danger-color-dark',

    // Backgrounds
    BG_WHITE: '--bg-white',
    BG_LIGHT: '--bg-light',
    BG_MEDIUM: '--bg-medium',

    // Text
    TEXT_PRIMARY: '--text-primary',
    TEXT_SECONDARY: '--text-secondary',
    TEXT_MUTED: '--text-muted',

    // Borders
    BORDER_LIGHT: '--border-light',
    BORDER_MEDIUM: '--border-medium',

    // Special
    INFO_ALT2: '--info-color-alt2',
    FOV: '--primary-color',
    SIMULATION_ACTIVE: '--color-simulation-active',
    SIMULATION_ERROR_RED: '--danger-bg',
    SIMULATION_ERROR_TEXT: '--danger-text',
    GEOSTATIONARY: '--color-geostationary',
    SAMPLING_GOOD: '--color-sampling-good',
    SAMPLING_OVERSAMPLED: '--color-sampling-oversampled',
    SAMPLING_SLIGHTLY_OVERSAMPLED: '--color-sampling-slightly-oversampled',
    NAV_PRIMARY: '--color-nav-primary',
    NAV_PRIMARY_HOVER: '--color-nav-primary-hover'
};

// ==========================================================================
// CONVENIENCE GETTERS
// ==========================================================================

/**
 * Get primary color
 * @returns {string} The computed primary color
 */
window.stylingUtils_getPrimaryColor = function() {
    return window.stylingUtils_getCssVar(
        window.stylingUtils_COLOR_VARS.PRIMARY,
        window.stylingUtils_getThemeFallback('PRIMARY_COLOR')
    );
};

/**
 * Get chart line color by index
 * @param {number} index - Chart line index (0-3)
 * @returns {string} The computed chart line color
 */
window.stylingUtils_getChartLineColor = function(index) {
    const varName = {
        0: window.stylingUtils_COLOR_VARS.CHART_LINE_1,
        1: window.stylingUtils_COLOR_VARS.CHART_LINE_2,
        2: window.stylingUtils_COLOR_VARS.CHART_LINE_3,
        3: window.stylingUtils_COLOR_VARS.CHART_LINE_4
    }[index] || window.stylingUtils_COLOR_VARS.CHART_LINE_PRIMARY;

    const fallbackKey = {
        0: 'CHART_LINE_BLUE',
        1: 'CHART_LINE_YELLOW',
        2: 'CHART_LINE_GRAY',
        3: 'CHART_LINE_MAGENTA'
    }[index] || 'CHART_LINE_PRIMARY';

    return window.stylingUtils_getCssVar(varName, window.stylingUtils_getThemeFallback(fallbackKey));
};

/**
 * Get text color for contrast against background
 * @param {string} bgColorVar - CSS variable name for background color
 * @returns {string} Either dark or light text color based on luminance
 */
window.stylingUtils_getContrastText = function(bgColorVar) {
    try {
        const bgColor = window.stylingUtils_getCssVar(bgColorVar, '#ffffff');
        // Simple luminance calculation
        const isDark = window.stylingUtils_isDarkColor(bgColor);
        return isDark
            ? window.stylingUtils_getCssVar(window.stylingUtils_COLOR_VARS.TEXT_WHITE, '#ffffff')
            : window.stylingUtils_getCssVar(window.stylingUtils_COLOR_VARS.TEXT_PRIMARY, window.stylingUtils_getThemeFallback('TEXT_PRIMARY'));
    } catch (e) {
        return window.stylingUtils_getThemeFallback('TEXT_PRIMARY');
    }
};

/**
 * Determine if a color is dark (for text contrast)
 * @param {string} color - Color in hex, rgb, or rgba format
 * @returns {boolean} True if color is dark
 */
window.stylingUtils_isDarkColor = function(color) {
    try {
        // Extract RGB values from various formats
        let r, g, b;

        if (color.startsWith('#')) {
            const hex = color.replace('#', '');
            if (hex.length === 3) {
                r = parseInt(hex[0] + hex[0], 16);
                g = parseInt(hex[1] + hex[1], 16);
                b = parseInt(hex[2] + hex[2], 16);
            } else if (hex.length === 6) {
                r = parseInt(hex.substring(0, 2), 16);
                g = parseInt(hex.substring(2, 4), 16);
                b = parseInt(hex.substring(4, 6), 16);
            } else {
                return false;
            }
        } else if (color.startsWith('rgb')) {
            const match = color.match(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/i);
            if (match) {
                r = parseInt(match[1], 10);
                g = parseInt(match[2], 10);
                b = parseInt(match[3], 10);
            } else {
                return false;
            }
        } else if (color.startsWith('rgba')) {
            const match = color.match(/rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)/i);
            if (match) {
                r = parseInt(match[1], 10);
                g = parseInt(match[2], 10);
                b = parseInt(match[3], 10);
            } else {
                return false;
            }
        } else {
            return false;
        }

        // Calculate luminance (relative luminance)
        const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
        return luminance < 0.5;
    } catch (e) {
        return false;
    }
};

/**
 * Get FOV color
 * @returns {string} The computed FOV color
 */
window.stylingUtils_getFovColor = function() {
    return window.stylingUtils_getCssVar(
        window.stylingUtils_COLOR_VARS.FOV,
        window.stylingUtils_getThemeFallback('FOV_COLOR')
    );
};

/**
 * Get success color
 * @returns {string} The computed success color
 */
window.stylingUtils_getSuccessColor = function() {
    return window.stylingUtils_getCssVar(
        window.stylingUtils_COLOR_VARS.SUCCESS,
        window.stylingUtils_getThemeFallback('SUCCESS_COLOR')
    );
};

/**
 * Get danger color
 * @returns {string} The computed danger color
 */
window.stylingUtils_getDangerColor = function() {
    return window.stylingUtils_getCssVar(
        window.stylingUtils_COLOR_VARS.DANGER,
        window.stylingUtils_getThemeFallback('DANGER_COLOR')
    );
};

/**
 * Get danger dark color
 * @returns {string} The computed danger dark color
 */
window.stylingUtils_getDangerDarkColor = function() {
    return window.stylingUtils_getCssVar(
        window.stylingUtils_COLOR_VARS.DANGER_DARK,
        window.stylingUtils_getThemeFallback('DANGER_COLOR_DARK')
    );
};

/**
 * Get geostationary belt color
 * @returns {string} The computed geostationary color
 */
window.stylingUtils_getGeostationaryColor = function() {
    return window.stylingUtils_getCssVar(
        window.stylingUtils_COLOR_VARS.GEOSTATIONARY,
        window.stylingUtils_getThemeFallback('GEOSTATIONARY_COLOR')
    );
};

/**
 * Get weather overlay color for cloud or seeing conditions
 * @param {number} condition - Condition level (1-9 for clouds, 1-8 for seeing)
 * @param {string} type - Either 'cloud' or 'seeing'
 * @returns {string} The computed color in rgba format
 */
window.stylingUtils_getWeatherColor = function(condition, type) {
    const currentTheme = window.stylingUtils_isDarkTheme() ? 'dark' : 'light';
    const fallbackMap = type === 'cloud' ? {
        1: window.stylingUtils_FALLBACKS[currentTheme].WEATHER_CLOUD_1,
        2: window.stylingUtils_FALLBACKS[currentTheme].WEATHER_CLOUD_2,
        3: window.stylingUtils_FALLBACKS[currentTheme].WEATHER_CLOUD_3,
        4: window.stylingUtils_FALLBACKS[currentTheme].WEATHER_CLOUD_4,
        5: window.stylingUtils_FALLBACKS[currentTheme].WEATHER_CLOUD_5,
        6: window.stylingUtils_FALLBACKS[currentTheme].WEATHER_CLOUD_6,
        7: window.stylingUtils_FALLBACKS[currentTheme].WEATHER_CLOUD_7,
        8: window.stylingUtils_FALLBACKS[currentTheme].WEATHER_CLOUD_8,
        9: window.stylingUtils_FALLBACKS[currentTheme].WEATHER_CLOUD_9
    } : {
        1: window.stylingUtils_FALLBACKS[currentTheme].WEATHER_SEEING_1,
        2: window.stylingUtils_FALLBACKS[currentTheme].WEATHER_SEEING_2,
        3: window.stylingUtils_FALLBACKS[currentTheme].WEATHER_SEEING_3,
        4: window.stylingUtils_FALLBACKS[currentTheme].WEATHER_SEEING_4,
        5: window.stylingUtils_FALLBACKS[currentTheme].WEATHER_SEEING_5,
        6: window.stylingUtils_FALLBACKS[currentTheme].WEATHER_SEEING_6,
        7: window.stylingUtils_FALLBACKS[currentTheme].WEATHER_SEEING_7,
        8: window.stylingUtils_FALLBACKS[currentTheme].WEATHER_SEEING_8
    };

    const varPrefix = type === 'cloud' ? '--weather-cloud-' : '--weather-seeing-';
    return fallbackMap[condition] || fallbackMap[1];
};

/**
 * Get simulation active color
 * @returns {string} The computed simulation active color (red)
 */
window.stylingUtils_getSimulationActiveColor = function() {
    return window.stylingUtils_getCssVar(
        window.stylingUtils_COLOR_VARS.SIMULATION_ACTIVE,
        window.stylingUtils_getThemeFallback('SIMULATION_ACTIVE')
    );
};

/**
 * Get sampling good color
 * @returns {string} The computed sampling good color (green)
 */
window.stylingUtils_getSamplingGoodColor = function() {
    return window.stylingUtils_getCssVar(
        window.stylingUtils_COLOR_VARS.SAMPLING_GOOD,
        window.stylingUtils_getThemeFallback('SAMPLING_GOOD')
    );
};

/**
 * Get sampling oversampled color
 * @returns {string} The computed sampling oversampled color (purple)
 */
window.stylingUtils_getSamplingOversampledColor = function() {
    return window.stylingUtils_getCssVar(
        window.stylingUtils_COLOR_VARS.SAMPLING_OVERSAMPLED,
        window.stylingUtils_getThemeFallback('SAMPLING_OVERSAMPLED')
    );
};

/**
 * Get sampling slightly oversampled color
 * @returns {string} The computed sampling slightly oversampled color (blue)
 */
window.stylingUtils_getSamplingSlightlyOversampledColor = function() {
    return window.stylingUtils_getCssVar(
        window.stylingUtils_COLOR_VARS.SAMPLING_SLIGHTLY_OVERSAMPLED,
        window.stylingUtils_getThemeFallback('SAMPLING_SLIGHTLY_OVERSAMPLED')
    );
};

/**
 * Get navigation primary color
 * @returns {string} The computed navigation primary color
 */
window.stylingUtils_getNavPrimaryColor = function() {
    return window.stylingUtils_getCssVar(
        window.stylingUtils_COLOR_VARS.NAV_PRIMARY,
        window.stylingUtils_getThemeFallback('PRIMARY_COLOR')
    );
};

/**
 * Get navigation primary hover color
 * @returns {string} The computed navigation primary hover color
 */
window.stylingUtils_getNavPrimaryHoverColor = function() {
    return window.stylingUtils_getCssVar(
        window.stylingUtils_COLOR_VARS.NAV_PRIMARY_HOVER,
        window.stylingUtils_getThemeFallback('PRIMARY_DARK')
    );
};

// ==========================================================================
// GLOBAL EXPORTS
// ==========================================================================

/**
 * Get a CSS variable value with fallback (convenience function)
 * This is the main entry point for color retrieval in other scripts
 * @param {string} varName - The CSS variable name (e.g., '--primary-color')
 * @param {string} fallback - Fallback value if variable is not defined
 * @returns {string} The computed value or fallback
 */
window.stylingUtils_getColor = function(varName, fallback = '') {
    return window.stylingUtils_getCssVar(varName, fallback);
};

/**
 * Get chart line 4 color (magenta) - specifically for secondary objects
 * @returns {string} The computed magenta color
 */
window.stylingUtils_getChartLine4Color = function() {
    return window.stylingUtils_getCssVar(
        window.stylingUtils_COLOR_VARS.CHART_LINE_4,
        window.stylingUtils_getThemeFallback('CHART_LINE_MAGENTA')
    );
};

// ==========================================================================
// THEME MANAGEMENT
// ==========================================================================

/**
 * Get the current theme ('light' or 'dark')
 * @returns {string} Current theme
 */
window.stylingUtils_getCurrentTheme = function() {
    return document.documentElement.getAttribute('data-theme') || 'light';
};

/**
 * Toggle between light and dark themes
 * This function:
 * 1. Switches the data-theme attribute on the html element
 * 2. Saves the preference to localStorage for persistence
 * 3. Dispatches a custom event for other components to react
 * @returns {string} New theme ('light' or 'dark')
 */
window.stylingUtils_toggleTheme = function() {
    try {
        var htmlElement = document.documentElement;
        var currentTheme = htmlElement.getAttribute('data-theme') || 'light';
        var newTheme = currentTheme === 'dark' ? 'light' : 'dark';

        // Apply the new theme
        htmlElement.setAttribute('data-theme', newTheme);

        // Save to localStorage for persistence
        localStorage.setItem('theme', newTheme);

        // Dispatch event for other components to react
        var event = new CustomEvent('themeChanged', { detail: { theme: newTheme } });
        window.dispatchEvent(event);

        return newTheme;
    } catch (e) {
        console.warn('[styling-utils] Failed to toggle theme:', e);
        return null;
    }
};

/**
 * Set a specific theme
 * @param {string} theme - 'light' or 'dark'
 * @returns {boolean} Success status
 */
window.stylingUtils_setTheme = function(theme) {
    try {
        if (theme !== 'light' && theme !== 'dark') {
            console.warn('[styling-utils] Invalid theme value:', theme);
            return false;
        }

        var htmlElement = document.documentElement;
        htmlElement.setAttribute('data-theme', theme);
        localStorage.setItem('theme', theme);

        // Dispatch event for other components to react
        var event = new CustomEvent('themeChanged', { detail: { theme: theme } });
        window.dispatchEvent(event);

        return true;
    } catch (e) {
        console.warn('[styling-utils] Failed to set theme:', e);
        return false;
    }
};

/**
 * Check if the current theme is dark
 * @returns {boolean} True if dark theme is active
 */
window.stylingUtils_isDarkTheme = function() {
    return window.stylingUtils_getCurrentTheme() === 'dark';
};

// Export to global window object for classic script usage
window.stylingUtils = {
    FALLBACKS: window.stylingUtils_FALLBACKS,
    COLOR_VARS: window.stylingUtils_COLOR_VARS,
    getCssVar: window.stylingUtils_getCssVar,
    getColor: window.stylingUtils_getColor,  // Main convenience function
    getCssVarAsRgb: window.stylingUtils_getCssVarAsRgb,
    getCssVarAsRgba: window.stylingUtils_getCssVarAsRgba,
    getPrimaryColor: window.stylingUtils_getPrimaryColor,
    getChartLineColor: window.stylingUtils_getChartLineColor,
    getChartLine4Color: window.stylingUtils_getChartLine4Color,
    getContrastText: window.stylingUtils_getContrastText,
    isDarkColor: window.stylingUtils_isDarkColor,
    getFovColor: window.stylingUtils_getFovColor,
    getSuccessColor: window.stylingUtils_getSuccessColor,
    getDangerColor: window.stylingUtils_getDangerColor,
    getDangerDarkColor: window.stylingUtils_getDangerDarkColor,
    getGeostationaryColor: window.stylingUtils_getGeostationaryColor,
    getWeatherColor: window.stylingUtils_getWeatherColor,
    getSimulationActiveColor: window.stylingUtils_getSimulationActiveColor,
    getSamplingGoodColor: window.stylingUtils_getSamplingGoodColor,
    getSamplingOversampledColor: window.stylingUtils_getSamplingOversampledColor,
    getSamplingSlightlyOversampledColor: window.stylingUtils_getSamplingSlightlyOversampledColor,
    getNavPrimaryColor: window.stylingUtils_getNavPrimaryColor,
    getNavPrimaryHoverColor: window.stylingUtils_getNavPrimaryHoverColor,
    // Theme management functions
    getCurrentTheme: window.stylingUtils_getCurrentTheme,
    toggleTheme: window.stylingUtils_toggleTheme,
    setTheme: window.stylingUtils_setTheme,
    isDarkTheme: window.stylingUtils_isDarkTheme,
    getThemeFallback: window.stylingUtils_getThemeFallback,
    onThemeChange: window.stylingUtils_onThemeChange
};
