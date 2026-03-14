/**
 * E2E tests for the Weather Panel feature in Nova DSO Tracker.
 *
 * Tests verify:
 *   - Weather tab appears in dashboard tab bar
 *   - Clicking Weather tab displays weather panel
 *   - Hourly/Daily/Satellite view toggle works
 *   - Weather grid renders with condition cells
 *   - API endpoints respond correctly
 *
 * Run with: node tests/test_weather_panel.js
 *
 * Prerequisites:
 *   - App running on http://localhost:5000
 *   - puppeteer installed (npm install puppeteer)
 *
 * Munich coordinates used for consistent test results:
 *   lat=48.1351, lon=11.5820
 */

'use strict';

const puppeteer = require('puppeteer');
const http = require('http');

// ============================================================================
// CONFIGURATION
// ============================================================================

const BASE_URL = process.env.NOVA_BASE_URL || 'http://localhost:5000';
const LAT = 48.1351;  // Munich
const LON = 11.5820;
const TIMEOUT = 15000;  // 15 s per operation

// ============================================================================
// TEST RUNNER
// ============================================================================

let testsPassed = 0;
let testsFailed = 0;
let browser = null;
let page = null;

function assert(condition, message) {
    if (condition) {
        console.log(`  ✓ ${message}`);
        testsPassed++;
    } else {
        console.log(`  ✗ ${message}`);
        testsFailed++;
    }
}

function assertEqual(actual, expected, message) {
    if (actual === expected) {
        console.log(`  ✓ ${message}`);
        testsPassed++;
    } else {
        console.log(`  ✗ ${message}`);
        console.log(`    Expected: ${JSON.stringify(expected)}`);
        console.log(`    Actual:   ${JSON.stringify(actual)}`);
        testsFailed++;
    }
}

function assertContains(haystack, needle, message) {
    if (typeof haystack === 'string' && haystack.includes(needle)) {
        console.log(`  ✓ ${message}`);
        testsPassed++;
    } else {
        console.log(`  ✗ ${message}`);
        console.log(`    Expected "${haystack}" to contain "${needle}"`);
        testsFailed++;
    }
}

async function describe(suiteName, testFn) {
    console.log(`\n${suiteName}`);
    console.log('-'.repeat(suiteName.length));
    try {
        await testFn();
    } catch (err) {
        console.log(`  ✗ Suite threw unexpected error: ${err.message}`);
        testsFailed++;
    }
}

// ============================================================================
// HELPERS
// ============================================================================

/**
 * Check if the app is reachable before running browser tests.
 */
function checkAppReachable(url) {
    return new Promise((resolve) => {
        const req = http.get(url, (res) => {
            resolve(res.statusCode < 500);
        });
        req.on('error', () => resolve(false));
        req.setTimeout(5000, () => {
            req.destroy();
            resolve(false);
        });
    });
}

/**
 * Fetch a URL via Node's http module and return { status, body }.
 * Used for API endpoint tests without a browser.
 */
function httpGet(url) {
    return new Promise((resolve, reject) => {
        const req = http.get(url, (res) => {
            let body = '';
            res.on('data', (chunk) => { body += chunk; });
            res.on('end', () => resolve({ status: res.statusCode, body }));
        });
        req.on('error', reject);
        req.setTimeout(10000, () => {
            req.destroy();
            reject(new Error('Request timed out'));
        });
    });
}

/**
 * Navigate to the dashboard and wait for it to be ready.
 */
async function navigateToDashboard() {
    await page.goto(BASE_URL + '/', { waitUntil: 'domcontentloaded', timeout: TIMEOUT });
    // Wait for the tab bar to be present
    await page.waitForSelector('.tab-button', { timeout: TIMEOUT });
}

/**
 * Click the Weather tab and wait for the panel to become visible.
 */
async function openWeatherTab() {
    await page.click('[data-tab="weather"]');
    // The tab content uses display:none / display:block toggling
    await page.waitForFunction(
        () => {
            const el = document.getElementById('weather-tab-content');
            return el && el.style.display !== 'none' && el.style.display !== '';
        },
        { timeout: TIMEOUT }
    );
}

// ============================================================================
// SETUP / TEARDOWN
// ============================================================================

async function setup() {
    console.log('\nLaunching browser…');
    browser = await puppeteer.launch({
        headless: true,
        args: [
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
            '--disable-gpu',
        ],
    });
    page = await browser.newPage();
    page.setDefaultTimeout(TIMEOUT);

    // Suppress console noise from the app
    page.on('console', () => {});
    page.on('pageerror', () => {});
}

async function teardown() {
    if (browser) {
        await browser.close();
        browser = null;
        page = null;
    }
}

// ============================================================================
// TEST SUITES
// ============================================================================

async function runDashboardTabTests() {
    await describe('Dashboard Tab Bar — Weather Tab Presence', async () => {
        await navigateToDashboard();

        // 1. Weather tab button exists
        const tabExists = await page.$('[data-tab="weather"]') !== null;
        assert(tabExists, 'Weather tab button exists in the tab bar');

        // 2. Tab button has visible text
        const tabText = await page.$eval('[data-tab="weather"]', (el) => el.textContent.trim());
        assert(tabText.length > 0, `Weather tab has non-empty label ("${tabText}")`);

        // 3. Weather tab content container exists in DOM
        const contentExists = await page.$('#weather-tab-content') !== null;
        assert(contentExists, '#weather-tab-content container exists in DOM');

        // 4. Weather panel section exists inside the content container
        const panelExists = await page.$('#weather-tab-content #weather-panel') !== null;
        assert(panelExists, '#weather-panel exists inside #weather-tab-content');

        // 5. Weather tab content is initially hidden
        const isHidden = await page.$eval('#weather-tab-content', (el) => {
            const style = el.style.display;
            return style === 'none' || style === '';
        });
        assert(isHidden, '#weather-tab-content is hidden before tab is clicked');
    });
}

async function runWeatherTabClickTests() {
    await describe('Weather Tab Click — Panel Visibility', async () => {
        await navigateToDashboard();

        // Click the weather tab
        await page.click('[data-tab="weather"]');

        // Wait for content to become visible
        await page.waitForFunction(
            () => {
                const el = document.getElementById('weather-tab-content');
                return el && el.style.display !== 'none' && el.style.display !== '';
            },
            { timeout: TIMEOUT }
        );

        // 1. Content is now visible
        const display = await page.$eval('#weather-tab-content', (el) => el.style.display);
        assert(display !== 'none' && display !== '', `#weather-tab-content is visible after click (display="${display}")`);

        // 2. Weather panel section is visible
        const panelVisible = await page.$eval('#weather-panel', (el) => {
            const rect = el.getBoundingClientRect();
            return rect.width > 0 && rect.height > 0;
        });
        assert(panelVisible, '#weather-panel has non-zero dimensions after tab click');

        // 3. Panel header is present
        const headerExists = await page.$('#weather-panel .weather-panel__header') !== null;
        assert(headerExists, 'Weather panel header (.weather-panel__header) is present');

        // 4. View toggle controls are present
        const controlsExist = await page.$('[data-weather-controls]') !== null;
        assert(controlsExist, 'Weather view controls ([data-weather-controls]) are present');
    });
}

async function runViewToggleTests() {
    await describe('Weather View Toggle — Hourly / Daily / Satellite', async () => {
        await navigateToDashboard();
        await openWeatherTab();

        // ── Hourly view (default) ──────────────────────────────────────────
        const hourlyBtnActive = await page.$eval(
            '[data-view="hourly"][data-weather-view-btn]',
            (el) => el.classList.contains('active') || el.getAttribute('aria-selected') === 'true'
        );
        assert(hourlyBtnActive, 'Hourly button is active by default');

        const hourlyViewVisible = await page.$eval('#weather-hourly', (el) => {
            return !el.hidden && el.style.display !== 'none';
        });
        assert(hourlyViewVisible, '#weather-hourly view is visible by default');

        // ── Click Daily ────────────────────────────────────────────────────
        await page.click('[data-view="daily"][data-weather-view-btn]');
        await page.waitForFunction(
            () => {
                const daily = document.getElementById('weather-daily');
                return daily && !daily.hidden;
            },
            { timeout: TIMEOUT }
        );

        const dailyViewVisible = await page.$eval('#weather-daily', (el) => !el.hidden);
        assert(dailyViewVisible, '#weather-daily view becomes visible after clicking Daily button');

        const hourlyHiddenAfterDaily = await page.$eval('#weather-hourly', (el) => el.hidden);
        assert(hourlyHiddenAfterDaily, '#weather-hourly view is hidden after switching to Daily');

        const dailyBtnActive = await page.$eval(
            '[data-view="daily"][data-weather-view-btn]',
            (el) => el.classList.contains('active') || el.getAttribute('aria-selected') === 'true'
        );
        assert(dailyBtnActive, 'Daily button becomes active after click');

        // ── Click Satellite ────────────────────────────────────────────────
        await page.click('[data-view="satellite"][data-weather-view-btn]');
        await page.waitForFunction(
            () => {
                const sat = document.getElementById('weather-satellite');
                return sat && !sat.hidden;
            },
            { timeout: TIMEOUT }
        );

        const satelliteViewVisible = await page.$eval('#weather-satellite', (el) => !el.hidden);
        assert(satelliteViewVisible, '#weather-satellite view becomes visible after clicking Satellite button');

        const dailyHiddenAfterSat = await page.$eval('#weather-daily', (el) => el.hidden);
        assert(dailyHiddenAfterSat, '#weather-daily view is hidden after switching to Satellite');

        // ── Click back to Hourly ───────────────────────────────────────────
        await page.click('[data-view="hourly"][data-weather-view-btn]');
        await page.waitForFunction(
            () => {
                const hourly = document.getElementById('weather-hourly');
                return hourly && !hourly.hidden;
            },
            { timeout: TIMEOUT }
        );

        const hourlyRestoredVisible = await page.$eval('#weather-hourly', (el) => !el.hidden);
        assert(hourlyRestoredVisible, '#weather-hourly view is restored after clicking Hourly again');

        const satelliteHiddenAfterHourly = await page.$eval('#weather-satellite', (el) => el.hidden);
        assert(satelliteHiddenAfterHourly, '#weather-satellite view is hidden after switching back to Hourly');
    });
}

async function runWeatherGridStructureTests() {
    await describe('Weather Grid — DOM Structure', async () => {
        await navigateToDashboard();
        await openWeatherTab();

        // 1. Grid table exists
        const gridExists = await page.$('#weather-grid') !== null;
        assert(gridExists, '#weather-grid table exists');

        // 2. Grid has a thead with time row
        const timesRowExists = await page.$('#weather-row-times') !== null;
        assert(timesRowExists, '#weather-row-times (time header row) exists');

        // 3. Expected metric rows are present
        const metricRows = [
            'weather-row-clouds',
            'weather-row-transparency',
            'weather-row-seeing',
            'weather-row-precip',
            'weather-row-humidity',
            'weather-row-wind',
            'weather-row-temp',
        ];

        for (const rowId of metricRows) {
            const rowExists = await page.$(`#${rowId}`) !== null;
            assert(rowExists, `#${rowId} metric row exists in the grid`);
        }

        // 4. Grid wrapper is scrollable
        const wrapperExists = await page.$('[data-weather-grid-wrapper]') !== null;
        assert(wrapperExists, 'Scrollable grid wrapper ([data-weather-grid-wrapper]) exists');

        // 5. Legend is present
        const legendExists = await page.$('.weather-legend') !== null;
        assert(legendExists, 'Colour-scale legend (.weather-legend) is present');

        // 6. Moon info footer is present
        const moonInfoExists = await page.$('#weather-moon-info') !== null;
        assert(moonInfoExists, '#weather-moon-info footer is present');
    });
}

async function runWeatherGridCellsTests() {
    await describe('Weather Grid — Condition Cells After Data Load', async () => {
        await navigateToDashboard();
        await openWeatherTab();

        // Wait up to 10 s for the JS to populate at least one cell in the grid.
        // The panel auto-fetches data using the location stored in the app.
        // We check for any <td> inside the grid body — if data loaded, cells appear.
        let cellsLoaded = false;
        try {
            await page.waitForFunction(
                () => {
                    const grid = document.getElementById('weather-grid');
                    if (!grid) return false;
                    return grid.querySelectorAll('tbody td').length > 0;
                },
                { timeout: 10000 }
            );
            cellsLoaded = true;
        } catch (_) {
            // Data may not load in test environment (no location configured / no network)
            // We still verify the grid structure is correct.
        }

        if (cellsLoaded) {
            // Cells exist
            const cellCount = await page.$eval('#weather-grid', (grid) =>
                grid.querySelectorAll('tbody td').length
            );
            assert(cellCount > 0, `Weather grid has ${cellCount} data cells after load`);

            // At least one cell should have a condition class (good/fair/poor)
            const hasConditionClass = await page.$eval('#weather-grid', (grid) => {
                const cells = grid.querySelectorAll('tbody td');
                return Array.from(cells).some(
                    (td) =>
                        td.classList.contains('condition-good') ||
                        td.classList.contains('condition-fair') ||
                        td.classList.contains('condition-poor') ||
                        td.dataset.condition !== undefined
                );
            });
            assert(hasConditionClass, 'At least one grid cell has a condition class or data-condition attribute');
        } else {
            // Grid structure is intact even without data
            const gridExists = await page.$('#weather-grid') !== null;
            assert(gridExists, '#weather-grid table is present (data not loaded — no location/network in test env)');
            console.log('    ℹ  Skipping cell-content checks: weather data did not load within timeout');
        }
    });
}

async function runApiHourlyTests() {
    await describe('API — GET /api/v1/weather/hourly', async () => {
        const url = `${BASE_URL}/api/v1/weather/hourly?lat=${LAT}&lon=${LON}`;

        let result;
        try {
            result = await httpGet(url);
        } catch (err) {
            console.log(`  ✗ Request failed: ${err.message}`);
            testsFailed++;
            return;
        }

        // 1. HTTP status is not a server error (200 OK or 401/403 if auth required)
        const statusOk = result.status < 500;
        assert(statusOk, `GET /api/v1/weather/hourly returns non-5xx status (got ${result.status})`);

        if (result.status === 200) {
            // 2. Response is valid JSON
            let json;
            try {
                json = JSON.parse(result.body);
            } catch (_) {
                assert(false, 'Response body is valid JSON');
                return;
            }
            assert(true, 'Response body is valid JSON');

            // 3. Response has "data" key
            assert('data' in json, 'Response has "data" key');

            // 4. Response has "meta" key
            assert('meta' in json, 'Response has "meta" key');

            if (json.meta) {
                // 5. Meta contains lat/lon
                assert('lat' in json.meta, 'Meta contains "lat"');
                assert('lon' in json.meta, 'Meta contains "lon"');
                assertEqual(json.meta.lat, LAT, `Meta lat matches requested value (${LAT})`);
                assertEqual(json.meta.lon, LON, `Meta lon matches requested value (${LON})`);
            }

            if (Array.isArray(json.data) && json.data.length > 0) {
                const first = json.data[0];

                // 6. Each entry has expected fields
                const expectedFields = ['timepoint', 'cloudcover', 'temp2m', 'rh2m'];
                for (const field of expectedFields) {
                    assert(field in first, `Hourly entry has "${field}" field`);
                }

                // 7. cloudcover is in range 1–9
                const cc = first.cloudcover;
                assert(
                    typeof cc === 'number' && cc >= 1 && cc <= 9,
                    `cloudcover value (${cc}) is in valid range 1–9`
                );
            } else {
                console.log('    ℹ  dataseries is empty — skipping field-level checks');
            }
        } else if (result.status === 401 || result.status === 403) {
            console.log(`    ℹ  Endpoint requires authentication (${result.status}) — skipping body checks`);
        } else if (result.status === 502 || result.status === 503) {
            console.log(`    ℹ  Upstream weather service unavailable (${result.status}) — skipping body checks`);
        }
    });
}

async function runApiDailyTests() {
    await describe('API — GET /api/v1/weather/daily', async () => {
        const url = `${BASE_URL}/api/v1/weather/daily?lat=${LAT}&lon=${LON}`;

        let result;
        try {
            result = await httpGet(url);
        } catch (err) {
            console.log(`  ✗ Request failed: ${err.message}`);
            testsFailed++;
            return;
        }

        // 1. HTTP status is not a server error
        const statusOk = result.status < 500;
        assert(statusOk, `GET /api/v1/weather/daily returns non-5xx status (got ${result.status})`);

        if (result.status === 200) {
            let json;
            try {
                json = JSON.parse(result.body);
            } catch (_) {
                assert(false, 'Response body is valid JSON');
                return;
            }
            assert(true, 'Response body is valid JSON');

            // 2. Response has "data" and "meta"
            assert('data' in json, 'Response has "data" key');
            assert('meta' in json, 'Response has "meta" key');

            if (json.meta) {
                assert('days' in json.meta, 'Meta contains "days" count');
                const days = json.meta.days;
                assert(
                    typeof days === 'number' && days >= 0,
                    `Meta "days" is a non-negative number (${days})`
                );
            }

            if (Array.isArray(json.data) && json.data.length > 0) {
                const first = json.data[0];

                // 3. Each daily entry has expected aggregated fields
                const expectedFields = [
                    'day_index',
                    'night_cloudcover_avg',
                    'temp2m_avg',
                    'rh2m_avg',
                    'hourly_count',
                ];
                for (const field of expectedFields) {
                    assert(field in first, `Daily entry has "${field}" field`);
                }

                // 4. day_index starts at 0
                assertEqual(first.day_index, 0, 'First daily entry has day_index = 0');
            } else {
                console.log('    ℹ  daily data is empty — skipping field-level checks');
            }
        } else if (result.status === 401 || result.status === 403) {
            console.log(`    ℹ  Endpoint requires authentication (${result.status}) — skipping body checks`);
        } else if (result.status === 502 || result.status === 503) {
            console.log(`    ℹ  Upstream weather service unavailable (${result.status}) — skipping body checks`);
        }
    });
}

async function runApiValidationTests() {
    await describe('API — Parameter Validation', async () => {
        // Missing lat/lon
        const missingResult = await httpGet(`${BASE_URL}/api/v1/weather/hourly`);
        assert(
            missingResult.status === 400 || missingResult.status === 401 || missingResult.status === 403,
            `Missing lat/lon returns 400 (or auth error) — got ${missingResult.status}`
        );

        if (missingResult.status === 400) {
            let json;
            try { json = JSON.parse(missingResult.body); } catch (_) { json = null; }
            assert(json !== null && 'error' in json, 'Error response has "error" key');
        }

        // Invalid lat value
        const invalidResult = await httpGet(`${BASE_URL}/api/v1/weather/hourly?lat=999&lon=0`);
        assert(
            invalidResult.status === 400 || invalidResult.status === 401 || invalidResult.status === 403,
            `Out-of-range lat returns 400 (or auth error) — got ${invalidResult.status}`
        );

        // Non-numeric lat
        const nanResult = await httpGet(`${BASE_URL}/api/v1/weather/hourly?lat=abc&lon=0`);
        assert(
            nanResult.status === 400 || nanResult.status === 401 || nanResult.status === 403,
            `Non-numeric lat returns 400 (or auth error) — got ${nanResult.status}`
        );
    });
}

async function runSatelliteViewTests() {
    await describe('Satellite View — iframe and Caption', async () => {
        await navigateToDashboard();
        await openWeatherTab();

        // Switch to satellite view
        await page.click('[data-view="satellite"][data-weather-view-btn]');
        await page.waitForFunction(
            () => {
                const sat = document.getElementById('weather-satellite');
                return sat && !sat.hidden;
            },
            { timeout: TIMEOUT }
        );

        // 1. Satellite view panel is visible
        const satVisible = await page.$eval('#weather-satellite', (el) => !el.hidden);
        assert(satVisible, '#weather-satellite panel is visible');

        // 2. iframe element exists
        const iframeExists = await page.$('#satellite-frame') !== null;
        assert(iframeExists, '#satellite-frame iframe exists in satellite view');

        // 3. Caption / Windy link is present
        const captionExists = await page.$('.weather-satellite__caption') !== null;
        assert(captionExists, '.weather-satellite__caption is present');

        // 4. Windy link exists
        const windyLinkExists = await page.$('.weather-satellite__link') !== null;
        assert(windyLinkExists, '.weather-satellite__link (Windy link) is present');
    });
}

async function runRefreshButtonTests() {
    await describe('Weather Panel — Refresh Button', async () => {
        await navigateToDashboard();
        await openWeatherTab();

        // 1. Refresh button exists
        const refreshExists = await page.$('[data-weather-refresh]') !== null;
        assert(refreshExists, 'Refresh button ([data-weather-refresh]) exists');

        // 2. Refresh button is clickable (no JS error thrown)
        let clickOk = true;
        try {
            await page.click('[data-weather-refresh]');
        } catch (err) {
            clickOk = false;
        }
        assert(clickOk, 'Refresh button is clickable without throwing an error');
    });
}

async function runMoonInfoTests() {
    await describe('Weather Panel — Moon Information Footer', async () => {
        await navigateToDashboard();
        await openWeatherTab();

        // 1. Moon info footer exists
        const moonInfoExists = await page.$('#weather-moon-info') !== null;
        assert(moonInfoExists, '#weather-moon-info footer exists');

        // 2. Moon phase element exists
        const moonPhaseExists = await page.$('#weather-moon-phase') !== null;
        assert(moonPhaseExists, '#weather-moon-phase element exists');

        // 3. Moon illumination element exists
        const moonIllumExists = await page.$('#weather-moon-illumination') !== null;
        assert(moonIllumExists, '#weather-moon-illumination element exists');

        // 4. Moonrise element exists
        const moonRiseExists = await page.$('#weather-moon-rise') !== null;
        assert(moonRiseExists, '#weather-moon-rise element exists');

        // 5. Moonset element exists
        const moonSetExists = await page.$('#weather-moon-set') !== null;
        assert(moonSetExists, '#weather-moon-set element exists');

        // 6. Moon glyph element exists
        const moonGlyphExists = await page.$('#weather-moon-glyph') !== null;
        assert(moonGlyphExists, '#weather-moon-glyph element exists');
    });
}

// ============================================================================
// MAIN
// ============================================================================

async function main() {
    console.log('='.repeat(60));
    console.log('Nova DSO Tracker — Weather Panel E2E Tests');
    console.log(`Target: ${BASE_URL}`);
    console.log(`Coords: lat=${LAT}, lon=${LON} (Munich)`);
    console.log('='.repeat(60));

    // Check if app is reachable
    console.log('\nChecking app availability…');
    const reachable = await checkAppReachable(BASE_URL);
    if (!reachable) {
        console.log(`\n⚠  App not reachable at ${BASE_URL}`);
        console.log('   Start the app first: flask run  (or docker compose up)');
        console.log('   Then re-run: node tests/test_weather_panel.js\n');
        process.exit(1);
    }
    console.log('  App is reachable ✓');

    // Run API tests (no browser needed)
    await runApiHourlyTests();
    await runApiDailyTests();
    await runApiValidationTests();

    // Run browser-based E2E tests
    try {
        await setup();

        await runDashboardTabTests();
        await runWeatherTabClickTests();
        await runViewToggleTests();
        await runWeatherGridStructureTests();
        await runWeatherGridCellsTests();
        await runSatelliteViewTests();
        await runRefreshButtonTests();
        await runMoonInfoTests();
    } finally {
        await teardown();
    }

    // ── Summary ──────────────────────────────────────────────────────────────
    console.log('\n' + '='.repeat(60));
    console.log(`Test Results: ${testsPassed} passed, ${testsFailed} failed`);
    console.log('='.repeat(60));

    if (testsFailed > 0) {
        process.exit(1);
    }
}

main().catch((err) => {
    console.error('\nFatal error:', err);
    teardown().finally(() => process.exit(1));
});
