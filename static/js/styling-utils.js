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
window.stylingUtils_FALLBACKS = {
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
    SIMULATION_GREEN: '#ca0e0e',
    SIMULATION_ERROR_RED: '#f8d7da',
    SIMULATION_ERROR_TEXT: '#721c24',
    GEOSTATIONARY_COLOR: '#e056fd',

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
    SIMULATION_GREEN: '--success-color',
    SIMULATION_ERROR_RED: '--danger-bg',
    SIMULATION_ERROR_TEXT: '--danger-text',
    GEOSTATIONARY: '--color-geostationary'
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
        window.stylingUtils_FALLBACKS.PRIMARY_COLOR
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

    const fallback = {
        0: window.stylingUtils_FALLBACKS.CHART_LINE_BLUE,
        1: window.stylingUtils_FALLBACKS.CHART_LINE_YELLOW,
        2: window.stylingUtils_FALLBACKS.CHART_LINE_GRAY,
        3: window.stylingUtils_FALLBACKS.CHART_LINE_MAGENTA
    }[index] || window.stylingUtils_FALLBACKS.CHART_LINE_PRIMARY;

    return window.stylingUtils_getCssVar(varName, fallback);
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
            : window.stylingUtils_getCssVar(window.stylingUtils_COLOR_VARS.TEXT_PRIMARY, window.stylingUtils_FALLBACKS.TEXT_PRIMARY);
    } catch (e) {
        return window.stylingUtils_FALLBACKS.TEXT_PRIMARY;
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
        window.stylingUtils_FALLBACKS.FOV_COLOR
    );
};

/**
 * Get success color
 * @returns {string} The computed success color
 */
window.stylingUtils_getSuccessColor = function() {
    return window.stylingUtils_getCssVar(
        window.stylingUtils_COLOR_VARS.SUCCESS,
        window.stylingUtils_FALLBACKS.SUCCESS_COLOR
    );
};

/**
 * Get danger color
 * @returns {string} The computed danger color
 */
window.stylingUtils_getDangerColor = function() {
    return window.stylingUtils_getCssVar(
        window.stylingUtils_COLOR_VARS.DANGER,
        window.stylingUtils_FALLBACKS.DANGER_COLOR
    );
};

/**
 * Get danger dark color
 * @returns {string} The computed danger dark color
 */
window.stylingUtils_getDangerDarkColor = function() {
    return window.stylingUtils_getCssVar(
        window.stylingUtils_COLOR_VARS.DANGER_DARK,
        window.stylingUtils_FALLBACKS.DANGER_COLOR_DARK
    );
};

/**
 * Get geostationary belt color
 * @returns {string} The computed geostationary color
 */
window.stylingUtils_getGeostationaryColor = function() {
    return window.stylingUtils_getCssVar(
        window.stylingUtils_COLOR_VARS.GEOSTATIONARY,
        window.stylingUtils_FALLBACKS.GEOSTATIONARY_COLOR
    );
};

/**
 * Get weather overlay color for cloud or seeing conditions
 * @param {number} condition - Condition level (1-9 for clouds, 1-8 for seeing)
 * @param {string} type - Either 'cloud' or 'seeing'
 * @returns {string} The computed color in rgba format
 */
window.stylingUtils_getWeatherColor = function(condition, type) {
    const fallbackMap = type === 'cloud' ? {
        1: window.stylingUtils_FALLBACKS.WEATHER_CLOUD_1,
        2: window.stylingUtils_FALLBACKS.WEATHER_CLOUD_2,
        3: window.stylingUtils_FALLBACKS.WEATHER_CLOUD_3,
        4: window.stylingUtils_FALLBACKS.WEATHER_CLOUD_4,
        5: window.stylingUtils_FALLBACKS.WEATHER_CLOUD_5,
        6: window.stylingUtils_FALLBACKS.WEATHER_CLOUD_6,
        7: window.stylingUtils_FALLBACKS.WEATHER_CLOUD_7,
        8: window.stylingUtils_FALLBACKS.WEATHER_CLOUD_8,
        9: window.stylingUtils_FALLBACKS.WEATHER_CLOUD_9
    } : {
        1: window.stylingUtils_FALLBACKS.WEATHER_SEEING_1,
        2: window.stylingUtils_FALLBACKS.WEATHER_SEEING_2,
        3: window.stylingUtils_FALLBACKS.WEATHER_SEEING_3,
        4: window.stylingUtils_FALLBACKS.WEATHER_SEEING_4,
        5: window.stylingUtils_FALLBACKS.WEATHER_SEEING_5,
        6: window.stylingUtils_FALLBACKS.WEATHER_SEEING_6,
        7: window.stylingUtils_FALLBACKS.WEATHER_SEEING_7,
        8: window.stylingUtils_FALLBACKS.WEATHER_SEEING_8
    };

    const varPrefix = type === 'cloud' ? '--weather-cloud-' : '--weather-seeing-';
    return fallbackMap[condition] || fallbackMap[1];
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
        window.stylingUtils_FALLBACKS.CHART_LINE_MAGENTA
    );
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
    getWeatherColor: window.stylingUtils_getWeatherColor
};
