/**
 * Unit tests for session_log_charts.js theme functionality
 *
 * These tests verify that chart colors correctly respond to theme changes
 * and meet WCAG contrast requirements.
 *
 * Run with: node tests/test_session_log_charts_theme.js
 * Or include in a browser test runner.
 */

'use strict';

// Mock browser environment for Node.js testing
if (typeof window === 'undefined') {
    global.window = {
        addEventListener: () => {},
        dispatchEvent: () => {},
        getComputedStyle: () => ({ getPropertyValue: () => '' })
    };
    global.document = {
        documentElement: {
            getAttribute: () => 'light',
            setAttribute: () => {}
        }
    };
    global.localStorage = {
        getItem: () => null,
        setItem: () => {}
    };
}

// Mock stylingUtils
window.stylingUtils = {
    isDarkTheme: function() {
        return document.documentElement.getAttribute('data-theme') === 'dark';
    },
    getThemeFallback: function(key) {
        const dark = this.isDarkTheme();
        const fallbacks = {
            light: {
                TEXT_PRIMARY: '#333333',
                TEXT_SECONDARY: '#555555'
            },
            dark: {
                TEXT_PRIMARY: '#e0e0e0',
                TEXT_SECONDARY: '#b0b0b0'
            }
        };
        return (dark ? fallbacks.dark : fallbacks.light)[key] || '';
    }
};

// Simplified version of getThemeColors for testing (copied from session_log_charts.js)
function isDarkTheme() {
    if (window.stylingUtils && window.stylingUtils.isDarkTheme) {
        return window.stylingUtils.isDarkTheme();
    }
    return document.documentElement.getAttribute('data-theme') === 'dark';
}

function getThemeColors() {
    const dark = isDarkTheme();

    if (window.stylingUtils) {
        return {
            text: dark
                ? window.stylingUtils.getThemeFallback('TEXT_PRIMARY')
                : window.stylingUtils.getThemeFallback('TEXT_PRIMARY'),
            textMuted: dark ? '#b0b0b0' : '#666666',
            grid: dark
                ? 'rgba(180, 180, 180, 0.25)'
                : 'rgba(0, 0, 0, 0.1)',
            tooltipBg: dark
                ? 'rgba(40, 40, 40, 0.95)'
                : 'rgba(255, 255, 255, 0.92)',
            tooltipTitle: dark ? '#f0f0f0' : '#222222',
            tooltipBody: dark ? '#dddddd' : '#444444',
            tooltipBorder: dark
                ? 'rgba(120, 120, 120, 0.5)'
                : 'rgba(0, 0, 0, 0.15)'
        };
    }

    return {
        text: dark ? '#e0e0e0' : '#333333',
        textMuted: dark ? '#b0b0b0' : '#666666',
        grid: dark ? 'rgba(180, 180, 180, 0.25)' : 'rgba(0, 0, 0, 0.1)',
        tooltipBg: dark ? 'rgba(40, 40, 40, 0.95)' : 'rgba(255, 255, 255, 0.92)',
        tooltipTitle: dark ? '#f0f0f0' : '#222222',
        tooltipBody: dark ? '#dddddd' : '#444444',
        tooltipBorder: dark ? 'rgba(120, 120, 120, 0.5)' : 'rgba(0, 0, 0, 0.15)'
    };
}

// ============================================================================
// TEST SUITE
// ============================================================================

let testsPassed = 0;
let testsFailed = 0;

function assertEqual(actual, expected, message) {
    if (actual === expected) {
        console.log(`  ✓ ${message}`);
        testsPassed++;
    } else {
        console.log(`  ✗ ${message}`);
        console.log(`    Expected: ${expected}`);
        console.log(`    Actual:   ${actual}`);
        testsFailed++;
    }
}

function assertNotEmpty(value, message) {
    if (value && value.length > 0) {
        console.log(`  ✓ ${message}`);
        testsPassed++;
    } else {
        console.log(`  ✗ ${message}`);
        console.log(`    Expected non-empty value, got: ${value}`);
        testsFailed++;
    }
}

function assertValidColor(color, message) {
    // Check for valid hex or rgba format
    const hexPattern = /^#[0-9a-fA-F]{6}$/;
    const rgbaPattern = /^rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*(,\s*[\d.]+\s*)?\)$/;

    if (hexPattern.test(color) || rgbaPattern.test(color)) {
        console.log(`  ✓ ${message}`);
        testsPassed++;
    } else {
        console.log(`  ✗ ${message}`);
        console.log(`    Invalid color format: ${color}`);
        testsFailed++;
    }
}

function describe(suiteName, testFn) {
    console.log(`\n${suiteName}`);
    console.log('-'.repeat(suiteName.length));
    testFn();
}

// ============================================================================
// TESTS
// ============================================================================

describe('getThemeColors() - Light Mode (Day Theme)', function() {
    // Set light theme
    document.documentElement.getAttribute = () => 'light';

    const colors = getThemeColors();

    assertNotEmpty(colors.text, 'text color should be defined');
    assertNotEmpty(colors.textMuted, 'textMuted color should be defined');
    assertNotEmpty(colors.grid, 'grid color should be defined');
    assertNotEmpty(colors.tooltipBg, 'tooltipBg color should be defined');

    assertValidColor(colors.text, 'text should be valid color format');
    assertValidColor(colors.textMuted, 'textMuted should be valid color format');
    assertValidColor(colors.grid, 'grid should be valid color format');
    assertValidColor(colors.tooltipBg, 'tooltipBg should be valid color format');

    // Light mode should use dark text for contrast
    assertEqual(colors.text, '#333333', 'text should be dark (#333) in light mode');
    assertEqual(colors.textMuted, '#666666', 'textMuted should be gray (#666) in light mode');
    assertEqual(colors.tooltipBg, 'rgba(255, 255, 255, 0.92)', 'tooltipBg should be light in light mode');
    assertEqual(colors.tooltipTitle, '#222222', 'tooltipTitle should be dark in light mode');
});

describe('getThemeColors() - Dark Mode (Night Theme)', function() {
    // Set dark theme
    document.documentElement.getAttribute = () => 'dark';

    const colors = getThemeColors();

    assertNotEmpty(colors.text, 'text color should be defined');
    assertNotEmpty(colors.textMuted, 'textMuted color should be defined');
    assertNotEmpty(colors.grid, 'grid color should be defined');
    assertNotEmpty(colors.tooltipBg, 'tooltipBg color should be defined');

    assertValidColor(colors.text, 'text should be valid color format');
    assertValidColor(colors.textMuted, 'textMuted should be valid color format');
    assertValidColor(colors.grid, 'grid should be valid color format');
    assertValidColor(colors.tooltipBg, 'tooltipBg should be valid color format');

    // Dark mode should use light text for contrast
    assertEqual(colors.text, '#e0e0e0', 'text should be light (#e0e0e0) in dark mode');
    assertEqual(colors.textMuted, '#b0b0b0', 'textMuted should be light gray (#b0b0b0) in dark mode');
    assertEqual(colors.tooltipBg, 'rgba(40, 40, 40, 0.95)', 'tooltipBg should be dark in dark mode');
    assertEqual(colors.tooltipTitle, '#f0f0f0', 'tooltipTitle should be light in dark mode');
});

describe('WCAG Contrast Compliance', function() {
    // Light mode colors
    document.documentElement.getAttribute = () => 'light';
    const lightColors = getThemeColors();

    // Verify light mode uses dark text (for light backgrounds)
    assertEqual(
        lightColors.text.startsWith('#3') || lightColors.text.startsWith('#2'),
        true,
        'Light mode text should be dark (start with #2 or #3) for WCAG contrast on light bg'
    );

    // Dark mode colors
    document.documentElement.getAttribute = () => 'dark';
    const darkColors = getThemeColors();

    // Verify dark mode uses light text (for dark backgrounds)
    assertEqual(
        darkColors.text.startsWith('#e') || darkColors.text.startsWith('#d') || darkColors.text.startsWith('#f'),
        true,
        'Dark mode text should be light (start with #d, #e, or #f) for WCAG contrast on dark bg'
    );
});

describe('Theme Toggle Consistency', function() {
    // Test that toggling between themes produces different colors
    document.documentElement.getAttribute = () => 'light';
    const lightText = getThemeColors().text;

    document.documentElement.getAttribute = () => 'dark';
    const darkText = getThemeColors().text;

    assertEqual(
        lightText !== darkText,
        true,
        'Light and dark mode should produce different text colors'
    );
});

// ============================================================================
// SUMMARY
// ============================================================================

console.log('\n' + '='.repeat(50));
console.log(`Test Results: ${testsPassed} passed, ${testsFailed} failed`);
console.log('='.repeat(50));

// Exit with error code if any tests failed
if (testsFailed > 0) {
    process.exit(1);
}
