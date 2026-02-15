/**
 * framing-utils.js - Modular Framing Assistant Utilities
 *
 * Pure utility functions for framing assistant math, parsing, and calculations.
 * This module contains the "brain" of the framing assistant extracted into reusable,
 * testable functions with no side effects.
 *
 * @module framing-utils
 */

'use strict';

// ==========================================================================
// FRAMING CONSTANTS
// ==========================================================================

window.framingUtils_CONSTANTS = {
    // FOV defaults
    DEFAULT_FOV_DEG: 1.5,              // Default field of view in degrees

    // Rotation limits
    ROTATION_MIN_DEG: 0,               // Minimum rotation angle (degrees)
    ROTATION_MAX_DEG: 360,             // Maximum rotation angle (degrees)

    // Angle conversions
    ARCMIN_PER_DEG: 60,               // Arcminutes per degree
    RAD_PER_DEG: Math.PI / 180,        // Radians per degree
    DEG_PER_RAD: 180 / Math.PI,        // Degrees per radian

    // Weather/Time
    WEATHER_GROUPING_MS: 10800000,      // 3 hours in milliseconds for weather overlay

    // Mosaic defaults
    DEFAULT_MOSAIC_COLS: 1,
    DEFAULT_MOSAIC_ROWS: 1,
    DEFAULT_MOSAIC_OVERLAP_PCT: 10,   // 10% overlap

    // Zoom margins
    DEFAULT_ZOOM_MARGIN: 1.06,          // 6% margin for FOV zoom

    // Geo belt constants (Earth radius, Geostationary orbit radius)
    EARTH_RADIUS_KM: 6378,
    GEO_RADIUS_KM: 42164,

    // Cosine minimum to prevent division by zero near poles
    MIN_COS_DEC: 0.00175,             // Corresponds to ~89.9° declination

    // Geo belt point density
    GEO_BELT_RA_STEP_DEG: 0.2,        // Create a point every 0.2 degrees

    // Colors
    FOV_COLOR: '#83b4c5',
};

// ==========================================================================
// URL QUERY PARAMETER UTILITIES
// ==========================================================================

/**
 * Legacy survey URL mapping for upgrading old URLs
 * @private
 */
const LEGACY_SURVEY_MAPPING = {
    "https://www.simg.de/nebulae3/dr0_1/hbr8": "https://www.simg.de/nebulae3/dr0_2/hbr8",
    "https://www.simg.de/nebulae3/dr0_1/halpha8": "https://www.simg.de/nebulae3/dr0_2/halpha8",
    "https://www.simg.de/nebulae3/dr0_1/tc8": "https://www.simg.de/nebulae3/dr0_2/rgb8"
};

/**
 * Upgrade a legacy survey URL to its modern equivalent
 * @param {string} surveyUrl - The survey URL to upgrade
 * @returns {string} The upgraded URL or original if no mapping exists
 */
window.framingUtils_upgradeLegacySurveyUrl = function(surveyUrl) {
    if (!surveyUrl) return surveyUrl;
    const upgraded = LEGACY_SURVEY_MAPPING[surveyUrl];
    if (upgraded) {
        console.log("[framing-utils] Upgrading legacy survey URL:", surveyUrl);
    }
    return upgraded || surveyUrl;
};

/**
 * Parse query string parameters for framing assistant
 * Handles legacy survey URL mapping and extracts all framing-related parameters
 *
 * @param {string} queryString - URL query string (e.g., "?rig=1&ra=180&dec=45")
 * @returns {FramingQueryParams} Parsed parameters
 *
 * @typedef {Object} FramingQueryParams
 * @property {string|null} rig - Rig selection value
 * @property {number|null} ra - Right ascension in degrees
 * @property {number|null} dec - Declination in degrees
 * @property {number|null} rot - Rotation angle in degrees
 * @property {string|null} survey - Survey URL
 * @property {string|null} blend - Blend survey URL
 * @property {number|null} blendOp - Blend opacity (0-1)
 * @property {string|null} m_cols - Mosaic columns
 * @property {string|null} m_rows - Mosaic rows
 * @property {string|null} m_ov - Mosaic overlap percentage
 * @property {number|null} img_brightness - Image adjustment: brightness
 * @property {number|null} img_contrast - Image adjustment: contrast
 * @property {number|null} img_gamma - Image adjustment: gamma
 * @property {number|null} img_saturation - Image adjustment: saturation
 * @property {boolean|null} geo_belt_enabled - Geo belt overlay enabled flag
 */
window.framingUtils_parseFramingQueryString = function(queryString) {
    const q = new URLSearchParams(queryString || '');

    // Parse numeric values with proper fallbacks
    const parseOptionalFloat = (key) => {
        const val = q.get(key);
        return val !== null ? parseFloat(val) : null;
    };

    const parseOptionalBool = (key) => {
        const val = q.get(key);
        return val === '1' ? true : (val === '0' ? false : null);
    };

    return {
        rig: q.get('rig'),
        ra: parseOptionalFloat('ra'),
        dec: parseOptionalFloat('dec'),
        rot: parseOptionalFloat('rot'),
        survey: upgradeLegacySurveyUrl(q.get('survey')),
        blend: upgradeLegacySurveyUrl(q.get('blend')),
        blendOp: parseOptionalFloat('blend_op'),
        m_cols: q.get('m_cols'),
        m_rows: q.get('m_rows'),
        m_ov: q.get('m_ov'),
        img_brightness: parseOptionalFloat('img_b'),
        img_contrast: parseOptionalFloat('img_c'),
        img_gamma: parseOptionalFloat('img_g'),
        img_saturation: parseOptionalFloat('img_s'),
        geo_belt_enabled: parseOptionalBool('geo_belt'),
    };
}

/**
 * Build framing query string from state parameters
 *
 * @param {FramingState} state - The current framing state
 * @returns {string} Query string (e.g., "?rig=1&ra=180.000000&dec=45.000000")
 *
 * @typedef {Object} FramingState
 * @property {string} rig - Rig selection value
 * @property {number} ra - Right ascension in degrees
 * @property {number} dec - Declination in degrees
 * @property {number} rot - Rotation angle in degrees
 * @property {string} survey - Survey URL
 * @property {string} blend - Blend survey URL
 * @property {number} blendOp - Blend opacity (0-1)
 * @property {number} mosaicCols - Mosaic columns
 * @property {number} mosaicRows - Mosaic rows
 * @property {number} mosaicOverlap - Mosaic overlap percentage
 * @property {number} imgBrightness - Image adjustment: brightness
 * @property {number} imgContrast - Image adjustment: contrast
 * @property {number} imgGamma - Image adjustment: gamma
 * @property {number} imgSaturation - Image adjustment: saturation
 */
window.framingUtils_buildFramingQueryString = function(state) {
    const qp = new URLSearchParams();

    // Always include rig if provided
    if (state.rig) qp.set('rig', state.rig);

    // Include coordinates if finite
    if (Number.isFinite(state.ra)) qp.set('ra', state.ra.toFixed(6));
    if (Number.isFinite(state.dec)) qp.set('dec', state.dec.toFixed(6));

    // Rotation (normalized to 0-360)
    qp.set('rot', String(Math.round(normalizeAngle(state.rot))));

    // Survey settings
    if (state.survey) qp.set('survey', state.survey);
    if (state.blend) qp.set('blend', state.blend);
    qp.set('blend_op', String(Math.max(0, Math.min(1, state.blendOp))));

    // Mosaic params (only if non-single panel)
    if (state.mosaicCols > 1 || state.mosaicRows > 1) {
        qp.set('m_cols', state.mosaicCols);
        qp.set('m_rows', state.mosaicRows);
        qp.set('m_ov', state.mosaicOverlap);
    }

    // Image adjustments (only if non-default)
    if (state.imgBrightness !== 0) qp.set('img_b', state.imgBrightness.toFixed(2));
    if (state.imgContrast !== 0) qp.set('img_c', state.imgContrast.toFixed(2));
    if (state.imgGamma !== 1) qp.set('img_g', state.imgGamma.toFixed(2));
    if (state.imgSaturation !== 0) qp.set('img_s', state.imgSaturation.toFixed(2));

    return '?' + qp.toString();
}

// ==========================================================================
// ANGLE UTILITIES
// ==========================================================================

/**
 * Normalize an angle to the range [0, 360) degrees
 * @param {number} degrees - Angle in degrees
 * @returns {number} Normalized angle in [0, 360)
 */
window.framingUtils_normalizeAngle = function(degrees) {
    return ((degrees % 360) + 360) % 360;
}

/**
 * Convert degrees to radians
 * @param {number} degrees - Angle in degrees
 * @returns {number} Angle in radians
 */
window.framingUtils_degToRad = function(degrees) {
    return degrees * window.framingUtils_CONSTANTS.RAD_PER_DEG;
}

/**
 * Convert radians to degrees
 * @param {number} radians - Angle in radians
 * @returns {number} Angle in degrees
 */
window.framingUtils_radToDeg = function(radians) {
    return radians * window.framingUtils_CONSTANTS.DEG_PER_RAD;
}

/**
 * Convert arcminutes to degrees
 * @param {number} arcmin - Angle in arcminutes
 * @returns {number} Angle in degrees
 */
window.framingUtils_arcminToDeg = function(arcmin) {
    return arcmin / window.framingUtils_CONSTANTS.ARCMIN_PER_DEG;
}

/**
 * Convert degrees to arcminutes
 * @param {number} degrees - Angle in degrees
 * @returns {number} Angle in arcminutes
 */
window.framingUtils_degToArcmin = function(degrees) {
    return degrees * window.framingUtils_CONSTANTS.ARCMIN_PER_DEG;
}

/**
 * Convert RA hours to degrees
 * @param {number} hours - Right ascension in hours (0-24)
 * @returns {number} Right ascension in degrees (0-360)
 */
window.framingUtils_raHoursToDeg = function(hours) {
    return hours * 15;
}

/**
 * Convert RA degrees to hours
 * @param {number} degrees - Right ascension in degrees (0-360)
 * @returns {number} Right ascension in hours (0-24)
 */
window.framingUtils_raDegToHours = function(degrees) {
    return degrees / 15;
}

// ==========================================================================
// FOV CALCULATION UTILITIES
// ==========================================================================

/**
 * Calculate required zoom FOV based on panel dimensions and rotation
 *
 * @param {number} fovW_deg - Panel width in degrees
 * @param {number} fovH_deg - Panel height in degrees
 * @param {number} rotationDeg - Rotation angle in degrees
 * @param {number} aspectRatio - Viewport aspect ratio (width/height)
 * @param {number} margin - Margin multiplier (default: 1.06 for 6%)
 * @returns {number} Required width in degrees for the view
 */
window.framingUtils_calculateRequiredFov = function(fovW_deg, fovH_deg, rotationDeg, aspectRatio, margin = window.framingUtils_CONSTANTS.DEFAULT_ZOOM_MARGIN) {
    // Input validation
    if (!(isFinite(fovW_deg) && isFinite(fovH_deg) && fovW_deg > 0 && fovH_deg > 0)) {
        return NaN;
    }

    const th = degToRad(rotationDeg);

    // Calculate bounding box dimensions after rotation
    const needWidthDeg = Math.abs(fovW_deg * Math.cos(th)) + Math.abs(fovH_deg * Math.sin(th));
    const needHeightDeg = Math.abs(fovW_deg * Math.sin(th)) + Math.abs(fovH_deg * Math.cos(th));

    // Return the larger dimension with margin
    return Math.max(needWidthDeg * margin, needHeightDeg * margin * aspectRatio);
}

/**
 * Calculate effective FOV dimensions after accounting for mosaic grid and overlap
 *
 * @param {number} fovW_deg - Single panel width in degrees
 * @param {number} fovH_deg - Single panel height in degrees
 * @param {number} cols - Number of columns
 * @param {number} rows - Number of rows
 * @param {number} overlapPct - Overlap percentage (0-100)
 * @returns {Object} Total mosaic dimensions {width, height, stepW, stepH}
 */
window.framingUtils_calculateMosaicDimensions = function(fovW_deg, fovH_deg, cols, rows, overlapPct) {
    const overlap = overlapPct / 100;

    // Step sizes (effective width/height after overlap)
    const stepW = fovW_deg * (1 - overlap);
    const stepH = fovH_deg * (1 - overlap);

    // Total dimensions
    const totalW = fovW_deg + (cols - 1) * stepW;
    const totalH = fovH_deg + (rows - 1) * stepH;

    return { totalW, totalH, stepW, stepH };
}

/**
 * Calculate pane center offset from mosaic center
 *
 * @param {number} col - Column index (0-based)
 * @param {number} row - Row index (0-based)
 * @param {number} cols - Total number of columns
 * @param {number} rows - Total number of rows
 * @param {number} stepW - Horizontal step in degrees
 * @param {number} stepH - Vertical step in degrees
 * @returns {Object} Offset coordinates {cx_off, cy_off}
 */
window.framingUtils_calculatePaneOffset = function(col, row, cols, rows, stepW, stepH) {
    // Grid indexed from center: col=0 is left, col=cols-1 is right
    const cx_off = (col - (cols - 1) / 2) * stepW;
    // Grid indexed from center: row=0 is bottom, row=rows-1 is top
    const cy_off = (row - (rows - 1) / 2) * stepH;
    return { cx_off, cy_off };
}

/**
 * Apply 2D rotation to coordinates
 *
 * @param {number} x - X coordinate
 * @param {number} y - Y coordinate
 * @param {number} angleRad - Rotation angle in radians (counter-clockwise)
 * @returns {number[]} Rotated coordinates [x, y]
 */
window.framingUtils_rotate2d = function(x, y, angleRad) {
    return [
        x * Math.cos(angleRad) - y * Math.sin(angleRad),
        x * Math.sin(angleRad) + y * Math.cos(angleRad)
    ];
}

// ==========================================================================
// GNOMONIC PROJECTION UTILITIES
// ==========================================================================

/**
 * Calculate tangent plane basis vectors at a given RA/Dec point
 * Used for gnomonic projection calculations
 *
 * @param {number} ra0_deg - Center RA in degrees
 * @param {number} dec0_deg - Center Dec in degrees
 * @returns {Object} Basis vectors {cX, cY, cZ, eX, eY, eZ, nX, nY, nZ}
 */
window.framingUtils_calculateTangentPlaneBasis = function(ra0_deg, dec0_deg) {
    const ra0 = degToRad(ra0_deg);
    const dec0 = degToRad(dec0_deg);

    // Center unit vector
    const cX = Math.cos(dec0) * Math.cos(ra0);
    const cY = Math.cos(dec0) * Math.sin(ra0);
    const cZ = Math.sin(dec0);

    // East unit vector (tangent, points East)
    const eX = -Math.sin(ra0);
    const eY = Math.cos(ra0);
    const eZ = 0;

    // North unit vector (tangent, points North)
    const nX = -Math.sin(dec0) * Math.cos(ra0);
    const nY = -Math.sin(dec0) * Math.sin(ra0);
    const nZ = Math.cos(dec0);

    return { cX, cY, cZ, eX, eY, eZ, nX, nY, nZ };
}

/**
 * Project plane coordinates to sky coordinates using gnomonic projection
 *
 * @param {number} x_deg - X offset from center in degrees
 * @param {number} y_deg - Y offset from center in degrees
 * @param {number} ra0_deg - Center RA in degrees
 * @param {number} dec0_deg - Center Dec in degrees
 * @returns {number[]} Sky coordinates [ra, dec] in degrees
 */
window.framingUtils_planeToSkyGnomonic = function(x_deg, y_deg, ra0_deg, dec0_deg) {
    // Convert to radians
    const dx = degToRad(x_deg);
    const dy = degToRad(y_deg);
    const ra0 = degToRad(ra0_deg);
    const dec0 = degToRad(dec0_deg);

    const r = Math.hypot(dx, dy);

    // If offset is negligible, return center coordinates
    if (r < 1e-12) {
        return [ra0_deg, dec0_deg];
    }

    // Calculate tangent plane basis vectors
    const { cX, cY, cZ, eX, eY, eZ, nX, nY, nZ } = calculateTangentPlaneBasis(ra0_deg, dec0_deg);

    // Direction in tangent plane
    const dirX = (dx * eX + dy * nX) / r;
    const dirY = (dx * eY + dy * nY) / r;
    const dirZ = (dx * eZ + dy * nZ) / r;

    // Rotate from center to direction
    const s = Math.sin(r);
    const c = Math.cos(r);

    const pX = c * cX + s * dirX;
    const pY = c * cY + s * dirY;
    const pZ = c * cZ + s * dirZ;

    // Convert back to spherical coordinates
    let ra = Math.atan2(pY, pX);
    if (ra < 0) ra += 2 * Math.PI;
    const dec = Math.asin(pZ);

    return [radToDeg(ra), radToDeg(dec)];
}

/**
 * Project sky coordinates to plane coordinates using gnomonic projection
 * (Inverse of planeToSkyGnomonic)
 *
 * @param {number} ra_deg - Target RA in degrees
 * @param {number} dec_deg - Target Dec in degrees
 * @param {number} ra0_deg - Center RA in degrees
 * @param {number} dec0_deg - Center Dec in degrees
 * @returns {number[]|null} Plane coordinates [x, y] in degrees, or null if not in projection
 */
window.framingUtils_skyToPlaneGnomonic = function(ra_deg, dec_deg, ra0_deg, dec0_deg) {
    // Convert to radians
    const ra = degToRad(ra_deg);
    const dec = degToRad(dec_deg);
    const ra0 = degToRad(ra0_deg);
    const dec0 = degToRad(dec0_deg);

    // Check if point is in projection (cos(zenith_angle) > 0)
    const cosC = Math.sin(dec0) * Math.sin(dec) + Math.cos(dec0) * Math.cos(dec) * Math.cos(ra - ra0);
    if (cosC <= 0) {
        return null; // Point is beyond the projection horizon
    }

    // Gnomonic projection formula
    const k = 1 / cosC;
    const x = k * Math.cos(dec) * Math.sin(ra - ra0);
    const y = k * (Math.cos(dec0) * Math.sin(dec) - Math.sin(dec0) * Math.cos(dec) * Math.cos(ra - ra0));

    return [radToDeg(x), radToDeg(y)];
}

// ==========================================================================
// SPHERICAL STEPPING UTILITIES (N.I.N.A./ASIAIR COMPATIBLE)
// ==========================================================================

/**
 * Calculate pane coordinates using spherical stepping algorithm
 * This method is compatible with N.I.N.A. and ASIAIR mosaic planning
 *
 * @param {Object} params - Calculation parameters
 * @param {number} params.raCenterDeg - Center RA in degrees
 * @param {number} params.decCenterDeg - Center Dec in degrees
 * @param {number} params.fovW_deg - Panel width in degrees
 * @param {number} params.fovH_deg - Panel height in degrees
 * @param {number} params.cols - Number of columns
 * @param {number} params.rows - Number of rows
 * @param {number} params.overlapPct - Overlap percentage (0-100)
 * @param {number} params.rotDeg - Rotation angle in degrees
 * @returns {Array} Array of pane objects with {col, row, ra, dec}
 */
window.framingUtils_calculateMosaicPanesSpherical = function({
    raCenterDeg,
    decCenterDeg,
    fovW_deg,
    fovH_deg,
    cols,
    rows,
    overlapPct,
    rotDeg
}) {
    const panes = [];

    // Calculate step sizes with overlap
    const { stepW, stepH } = calculateMosaicDimensions(
        fovW_deg, fovH_deg, cols, rows, overlapPct
    );

    // Rotation angle (inverted for CSS vs math convention)
    const ang = -degToRad(rotDeg);

    for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
            // Calculate unrotated offset from mosaic center (in degrees)
            const { cx_off, cy_off } = calculatePaneOffset(
                c, r, cols, rows, stepW, stepH
            );

            // Apply 2D rotation matrix for Position Angle
            const rx = cx_off * Math.cos(ang) - cy_off * Math.sin(ang);
            const ry = cx_off * Math.sin(ang) + cy_off * Math.cos(ang);

            // SPHERICAL STEPPING ALGORITHM (N.I.N.A. / ASIAIR compatible)
            // Step 1: Calculate declination using spherical approximation
            const paneDecDeg = decCenterDeg + ry;

            // Step 2: Calculate RA offset with cosine correction at the panel's declination
            // RA spacing varies with 1/cos(Dec) due to converging meridians
            let cosDec;
            if (Math.abs(paneDecDeg) > 89.9) {
                cosDec = window.framingUtils_CONSTANTS.MIN_COS_DEC; // Prevent division by zero near poles
            } else {
                cosDec = Math.cos(degToRad(paneDecDeg));
            }
            const raOffsetDeg = rx / cosDec;

            // Step 3: Apply RA offset to center RA
            let paneRaDeg = raCenterDeg + raOffsetDeg;

            // Normalize RA to [0, 360) range
            paneRaDeg = normalizeAngle(paneRaDeg);

            panes.push({
                col: c,
                row: r,
                ra: paneRaDeg,
                dec: paneDecDeg
            });
        }
    }

    return panes;
}

/**
 * Calculate pane coordinates using gnomonic projection
 * This is the original algorithm (used for Aladin FOV overlay)
 *
 * @param {Object} params - Calculation parameters
 * @param {number} params.raCenterDeg - Center RA in degrees
 * @param {number} params.decCenterDeg - Center Dec in degrees
 * @param {number} params.fovW_deg - Panel width in degrees
 * @param {number} params.fovH_deg - Panel height in degrees
 * @param {number} params.cols - Number of columns
 * @param {number} params.rows - Number of rows
 * @param {number} params.overlapPct - Overlap percentage (0-100)
 * @param {number} params.rotDeg - Rotation angle in degrees
 * @returns {Array} Array of pane corner arrays [[ra1, dec1], [ra2, dec2], ...]
 */
window.framingUtils_calculateMosaicPanesGnomonic = function({
    raCenterDeg,
    decCenterDeg,
    fovW_deg,
    fovH_deg,
    cols,
    rows,
    overlapPct,
    rotDeg
}) {
    const panes = [];

    // Calculate step sizes with overlap
    const { stepW, stepH } = calculateMosaicDimensions(
        fovW_deg, fovH_deg, cols, rows, overlapPct
    );

    // Pane dimensions
    const halfW = fovW_deg / 2;
    const halfH = fovH_deg / 2;

    // Rotation angle (inverted for CSS vs math convention)
    const ang = -degToRad(rotDeg);

    for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
            // Calculate offset of this pane's center from mosaic center (unrotated)
            const { cx_off, cy_off } = calculatePaneOffset(
                c, r, cols, rows, stepW, stepH
            );

            // 4 Corners relative to this pane's center
            const corners = [
                [-halfW, -halfH], [halfW, -halfH], [halfW, halfH], [-halfW, halfH]
            ];

            // Calculate each corner's sky coordinates
            const polyCoords = corners.map(([kx, ky]) => {
                // Offset from pane center + Pane center offset from mosaic center
                const totalX = kx + cx_off;
                const totalY = ky + cy_off;

                // Rotate around mosaic center
                const [rx, ry] = rotate2d(totalX, totalY, ang);

                // Project to sky (negate X for RA direction)
                return planeToSkyGnomonic(-rx, ry, raCenterDeg, decCenterDeg);
            });

            panes.push(polyCoords);
        }
    }

    return panes;
}

// ==========================================================================
// NUDGE/COORDINATE UTILITIES
// ==========================================================================

/**
 * Calculate new center coordinates after nudging by RA/Dec offsets
 *
 * @param {Object} center - Current center coordinates {ra, dec} in degrees
 * @param {number} dxArcmin - RA offset in arcminutes (positive = West)
 * @param {number} dyArcmin - Dec offset in arcminutes (positive = North)
 * @returns {Object} New center coordinates {ra, dec} in degrees
 */
window.framingUtils_calculateNudgedCenter = function(center, dxArcmin, dyArcmin) {
    // Convert arcminutes to degrees
    const dxDeg = dxArcmin / window.framingUtils_CONSTANTS.ARCMIN_PER_DEG;
    const dyDeg = dyArcmin / window.framingUtils_CONSTANTS.ARCMIN_PER_DEG;

    const decRad = degToRad(center.dec);

    // RA offset varies with 1/cos(Dec) due to converging meridians
    let newRa = center.ra;
    if (Math.abs(decRad) < (Math.PI / 2.0 - 0.001)) {
        newRa -= dxDeg / Math.cos(decRad);
    }

    const newDec = center.dec + dyDeg;

    // Normalize RA to [0, 360)
    const normalizedRa = normalizeAngle(newRa);

    return { ra: normalizedRa, dec: newDec };
}

// ==========================================================================
// GEO BELT UTILITIES
// ==========================================================================

/**
 * Calculate geostationary satellite belt declination for a given observer latitude
 *
 * @param {number} observerLatDeg - Observer latitude in degrees
 * @returns {number} Apparent declination of geo belt in degrees
 */
window.framingUtils_calculateGeoBeltDeclination = function(observerLatDeg) {
    const latRad = degToRad(observerLatDeg);

    // Geostationary parallax calculation
    const num = window.framingUtils_CONSTANTS.EARTH_RADIUS_KM * Math.sin(latRad);
    const den = window.framingUtils_CONSTANTS.GEO_RADIUS_KM - (window.framingUtils_CONSTANTS.EARTH_RADIUS_KM * Math.cos(latRad));
    const parallaxRad = Math.atan2(num, den);

    // Geo belt appears on opposite side of celestial equator
    return -radToDeg(parallaxRad);
}

/**
 * Generate RA points for geo belt line
 *
 * @returns {number[]} Array of RA values in degrees from 0 to 360
 */
window.framingUtils_generateGeoBeltRaPoints = function() {
    const points = [];
    for (let ra = 0; ra < 360; ra += window.framingUtils_CONSTANTS.GEO_BELT_RA_STEP_DEG) {
        points.push(ra);
    }
    return points;
}

// ==========================================================================
// FORMAT UTILITIES
// ==========================================================================

/**
 * Format RA in hours:minutes:seconds format
 *
 * @param {number} raDeg - Right ascension in degrees (0-360)
 * @returns {string} Formatted RA (e.g., "12h 34m 56.7s")
 */
window.framingUtils_formatRaHms = function(raDeg) {
    const hours = raDeg / 15;
    const h = Math.floor(hours);
    const m = Math.floor((hours - h) * 60);
    const s = ((hours - h) * 60 - m) * 60;
    return `${h}h ${m}m ${s.toFixed(1)}s`;
}

/**
 * Format RA in hours:minutes format (for CSV export)
 *
 * @param {number} raDeg - Right ascension in degrees (0-360)
 * @returns {string} Formatted RA (e.g., "12h 34m")
 */
window.framingUtils_formatRaCsv = function(raDeg) {
    const hours = raDeg / 15;
    const h = Math.floor(hours);
    const m = Math.round((hours - h) * 60);
    return `${h}h ${m}m`;
}

/**
 * Format Dec in degrees:arcminutes format
 *
 * @param {number} decDeg - Declination in degrees
 * @returns {string} Formatted Dec (e.g., "+45° 30'")
 */
window.framingUtils_formatDecDm = function(decDeg) {
    const sign = decDeg >= 0 ? '+' : '-';
    const absDec = Math.abs(decDeg);
    const d = Math.floor(absDec);
    const m = Math.round((absDec - d) * 60);
    return `${sign}${d}° ${m}'`;
}

/**
 * Format Dec in degrees:arcminutes:seconds format
 *
 * @param {number} decDeg - Declination in degrees
 * @returns {string} Formatted Dec (e.g., "+45° 30' 15\"")
 */
window.framingUtils_formatDecDms = function(decDeg) {
    const sign = decDeg >= 0 ? '+' : '-';
    const absDec = Math.abs(decDeg);
    const d = Math.floor(absDec);
    const m = Math.floor((absDec - d) * 60);
    const s = ((absDec - d) * 60 - m) * 60;
    return `${sign}${d}° ${m}' ${s.toFixed(0)}"`;
}

/**
 * Format Dec in degrees:arcminutes format (for CSV export)
 *
 * @param {number} decDeg - Declination in degrees
 * @returns {string} Formatted Dec (e.g., "+45° 30m")
 */
window.framingUtils_formatDecCsv = function(decDeg) {
    const sign = decDeg >= 0 ? '+' : '-';
    const absDec = Math.abs(decDeg);
    const d = Math.floor(absDec);
    const m = Math.round((absDec - d) * 60);
    return `${sign}${d}° ${m}m`;
}

// ==========================================================================
// VALIDATION UTILITIES
// ==========================================================================

/**
 * JavaScript Number.isFinite() equivalent
 * @param {*} x - Value to check
 * @returns {boolean} True if finite number
 */
window.framingUtils_isFinite = function(x) {
    return typeof x === 'number' && !Number.isNaN(x);
}


/**
 * Validate that a coordinate is finite and within reasonable bounds
 *
 * @param {number} ra - Right ascension in degrees
 * @param {number} dec - Declination in degrees
 * @returns {boolean} True if valid
 */
window.framingUtils_isValidCoordinate = function(ra, dec) {
    return (
        isFinite(ra) && isFinite(dec) &&
        ra >= 0 && ra <= 360 &&
        dec >= -90 && dec <= 90
    );
}

/**
 * Validate FOV dimensions
 *
 * @param {number} fovW_deg - Width in degrees
 * @param {number} fovH_deg - Height in degrees
 * @returns {boolean} True if valid
 */
window.framingUtils_isValidFov = function(fovW_deg, fovH_deg) {
    return (
        isFinite(fovW_deg) && isFinite(fovH_deg) &&
        fovW_deg > 0 && fovH_deg > 0 &&
        fovW_deg <= 180 && fovH_deg <= 180
    );
}

/**
 * Validate rotation angle
 *
 * @param {number} rotationDeg - Rotation angle in degrees
 * @returns {boolean} True if valid
 */
window.framingUtils_isValidRotation = function(rotationDeg) {
    return (
        isFinite(rotationDeg) &&
        rotationDeg >= window.framingUtils_CONSTANTS.ROTATION_MIN_DEG &&
        rotationDeg <= window.framingUtils_CONSTANTS.ROTATION_MAX_DEG
    );
}

// ==========================================================================
// GLOBAL EXPORTS
// ==========================================================================

// Export to global window object for classic script usage
window.framingUtils = {
    CONSTANTS: window.framingUtils_CONSTANTS,
    upgradeLegacySurveyUrl: window.framingUtils_upgradeLegacySurveyUrl,
    parseFramingQueryString: window.framingUtils_parseFramingQueryString,
    buildFramingQueryString: window.framingUtils_buildFramingQueryString,
    normalizeAngle: window.framingUtils_normalizeAngle,
    degToRad: window.framingUtils_degToRad,
    radToDeg: window.framingUtils_radToDeg,
    arcminToDeg: window.framingUtils_arcminToDeg,
    degToArcmin: window.framingUtils_degToArcmin,
    raHoursToDeg: window.framingUtils_raHoursToDeg,
    raDegToHours: window.framingUtils_raDegToHours,
    calculateRequiredFov: window.framingUtils_calculateRequiredFov,
    calculateMosaicDimensions: window.framingUtils_calculateMosaicDimensions,
    calculatePaneOffset: window.framingUtils_calculatePaneOffset,
    rotate2d: window.framingUtils_rotate2d,
    calculateTangentPlaneBasis: window.framingUtils_calculateTangentPlaneBasis,
    planeToSkyGnomonic: window.framingUtils_planeToSkyGnomonic,
    skyToPlaneGnomonic: window.framingUtils_skyToPlaneGnomonic,
    calculateMosaicPanesSpherical: window.framingUtils_calculateMosaicPanesSpherical,
    calculateMosaicPanesGnomonic: window.framingUtils_calculateMosaicPanesGnomonic,
    calculateNudgedCenter: window.framingUtils_calculateNudgedCenter,
    calculateGeoBeltDeclination: window.framingUtils_calculateGeoBeltDeclination,
    generateGeoBeltRaPoints: window.framingUtils_generateGeoBeltRaPoints,
    formatRaHms: window.framingUtils_formatRaHms,
    formatRaCsv: window.framingUtils_formatRaCsv,
    formatDecDm: window.framingUtils_formatDecDm,
    formatDecDms: window.framingUtils_formatDecDms,
    formatDecCsv: window.framingUtils_formatDecCsv,
    isValidCoordinate: window.framingUtils_isValidCoordinate,
    isValidFov: window.framingUtils_isValidFov,
    isValidRotation: window.framingUtils_isValidRotation,
};
