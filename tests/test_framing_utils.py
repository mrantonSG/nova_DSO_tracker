"""
Comprehensive unit tests for static/js/framing-utils.js

This test suite validates JavaScript utility functions for the framing assistant
by implementing Python equivalents and verifying mathematical correctness.

Functions tested:
- Angle utilities (normalizeAngle, degToRad, radToDeg, arcminToDeg, etc.)
- FOV calculations (calculateRequiredFov, calculateMosaicDimensions)
- Mosaic math (calculatePaneOffset, rotate2d, spherical stepping)
- Nudge calculations (calculateNudgedCenter)
- Geo belt math (calculateGeoBeltDeclination, generateGeoBeltRaPoints)
- URL parsing (parseFramingQueryString, buildFramingQueryString)
- Formatting utilities (formatRaCsv, formatDecCsv, etc.)
- Validation (isValidCoordinate, isValidFov, isValidRotation)
- Edge cases (zero/negative FOV, invalid coords, rotation bounds)
"""
import math
import pytest

# ==========================================================================
# CONSTANTS (from framing-utils.js)
# ==========================================================================

class CONSTANTS:
    DEFAULT_FOV_DEG = 1.5
    ROTATION_MIN_DEG = 0
    ROTATION_MAX_DEG = 360
    ARCMIN_PER_DEG = 60
    RAD_PER_DEG = math.pi / 180
    DEG_PER_RAD = 180 / math.pi
    EARTH_RADIUS_KM = 6378
    GEO_RADIUS_KM = 42164
    MIN_COS_DEC = 0.00175

# ==========================================================================
# PYTHON IMPLEMENTATIONS OF JAVASCRIPT FUNCTIONS
# ==========================================================================

def normalize_angle(degrees: float) -> float:
    """Normalize an angle to the range [0, 360) degrees."""
    return ((degrees % 360) + 360) % 360

def deg_to_rad(degrees: float) -> float:
    """Convert degrees to radians."""
    return degrees * CONSTANTS.RAD_PER_DEG

def rad_to_deg(radians: float) -> float:
    """Convert radians to degrees."""
    return radians * CONSTANTS.DEG_PER_RAD

def arcmin_to_deg(arcmin: float) -> float:
    """Convert arcminutes to degrees."""
    return arcmin / CONSTANTS.ARCMIN_PER_DEG

def deg_to_arcmin(degrees: float) -> float:
    """Convert degrees to arcminutes."""
    return degrees * CONSTANTS.ARCMIN_PER_DEG

def ra_hours_to_deg(hours: float) -> float:
    """Convert RA hours to degrees."""
    return hours * 15

def ra_deg_to_hours(degrees: float) -> float:
    """Convert RA degrees to hours."""
    return degrees / 15

def calculate_required_fov(fov_w_deg: float, fov_h_deg: float,
                           rotation_deg: float, aspect_ratio: float,
                           margin: float = 1.06) -> float:
    """Calculate required zoom FOV based on panel dimensions and rotation."""
    if not (math.isfinite(fov_w_deg) and math.isfinite(fov_h_deg)
            and fov_w_deg > 0 and fov_h_deg > 0):
        return math.nan

    th = deg_to_rad(rotation_deg)

    # Calculate bounding box dimensions after rotation
    need_width_deg = abs(fov_w_deg * math.cos(th)) + abs(fov_h_deg * math.sin(th))
    need_height_deg = abs(fov_w_deg * math.sin(th)) + abs(fov_h_deg * math.cos(th))

    # Return the larger dimension with margin
    return max(need_width_deg * margin, need_height_deg * margin * aspect_ratio)

def calculate_mosaic_dimensions(fov_w_deg: float, fov_h_deg: float,
                                cols: int, rows: int, overlap_pct: float) -> dict:
    """Calculate effective FOV dimensions after accounting for overlap."""
    overlap = overlap_pct / 100.0

    # Step sizes (effective width/height after overlap)
    step_w = fov_w_deg * (1 - overlap)
    step_h = fov_h_deg * (1 - overlap)

    # Total dimensions
    total_w = fov_w_deg + (cols - 1) * step_w
    total_h = fov_h_deg + (rows - 1) * step_h

    return {'totalW': total_w, 'totalH': total_h, 'stepW': step_w, 'stepH': step_h}

def calculate_pane_offset(col: int, row: int, cols: int, rows: int,
                          step_w: float, step_h: float) -> dict:
    """Calculate pane center offset from mosaic center."""
    # Grid indexed from center: col=0 is left, col=cols-1 is right
    cx_off = (col - (cols - 1) / 2.0) * step_w
    # Grid indexed from center: row=0 is bottom, row=rows-1 is top
    cy_off = (row - (rows - 1) / 2.0) * step_h
    return {'cx_off': cx_off, 'cy_off': cy_off}

def rotate_2d(x: float, y: float, angle_rad: float) -> tuple:
    """Apply 2D rotation to coordinates."""
    return [
        x * math.cos(angle_rad) - y * math.sin(angle_rad),
        x * math.sin(angle_rad) + y * math.cos(angle_rad)
    ]

def calculate_nudged_center(ra: float, dec: float, dx_arcmin: float,
                            dy_arcmin: float) -> tuple:
    """Calculate new center coordinates after nudging by RA/Dec offsets."""
    # Convert arcminutes to degrees
    dx_deg = dx_arcmin / CONSTANTS.ARCMIN_PER_DEG
    dy_deg = dy_arcmin / CONSTANTS.ARCMIN_PER_DEG

    dec_rad = deg_to_rad(dec)

    # RA offset varies with 1/cos(Dec) due to converging meridians
    new_ra = ra
    if abs(dec_rad) < (math.pi / 2.0 - 0.001):
        new_ra -= dx_deg / math.cos(dec_rad)

    new_dec = dec + dy_deg

    # Normalize RA to [0, 360)
    normalized_ra = normalize_angle(new_ra)

    return (normalized_ra, new_dec)

def calculate_geo_belt_declination(observer_lat_deg: float) -> float:
    """Calculate geostationary satellite belt declination."""
    lat_rad = deg_to_rad(observer_lat_deg)

    # Geostationary parallax calculation
    num = CONSTANTS.EARTH_RADIUS_KM * math.sin(lat_rad)
    den = CONSTANTS.GEO_RADIUS_KM - (CONSTANTS.EARTH_RADIUS_KM * math.cos(lat_rad))
    parallax_rad = math.atan2(num, den)

    # Geo belt appears on opposite side of celestial equator
    return -rad_to_deg(parallax_rad)

def generate_geo_belt_ra_points() -> list:
    """Generate RA points for geo belt line."""
    ra_step_deg = 0.2
    points = []
    for ra in range(0, 360):
        points.append(ra)
    return points

def is_valid_coordinate(ra: float, dec: float) -> bool:
    """Validate that a coordinate is finite and within reasonable bounds."""
    return (
        math.isfinite(ra) and math.isfinite(dec) and
        ra >= 0 and ra <= 360 and
        dec >= -90 and dec <= 90
    )

def is_valid_fov(fov_w_deg: float, fov_h_deg: float) -> bool:
    """Validate FOV dimensions."""
    return (
        math.isfinite(fov_w_deg) and math.isfinite(fov_h_deg) and
        fov_w_deg > 0 and fov_h_deg > 0 and
        fov_w_deg <= 180 and fov_h_deg <= 180
    )

def is_valid_rotation(rotation_deg: float) -> bool:
    """Validate rotation angle."""
    return (
        math.isfinite(rotation_deg) and
        rotation_deg >= CONSTANTS.ROTATION_MIN_DEG and
        rotation_deg <= CONSTANTS.ROTATION_MAX_DEG
    )

# ==========================================================================
# TEST CLASSES
# ==========================================================================

class TestNormalizeAngle:
    """Test angle normalization utility."""

    def test_normalize_angle_zero(self):
        assert normalize_angle(0) == pytest.approx(0)

    def test_normalize_angle_positive(self):
        assert normalize_angle(90) == pytest.approx(90)
        assert normalize_angle(180) == pytest.approx(180)
        assert normalize_angle(270) == pytest.approx(270)

    def test_normalize_angle_wraps_at_360(self):
        assert normalize_angle(360) == pytest.approx(0)
        assert normalize_angle(400) == pytest.approx(40)
        assert normalize_angle(720) == pytest.approx(0)

    def test_normalize_angle_negative(self):
        assert normalize_angle(-90) == pytest.approx(270)
        assert normalize_angle(-180) == pytest.approx(180)
        assert normalize_angle(-270) == pytest.approx(90)
        assert normalize_angle(-360) == pytest.approx(0)

    def test_normalize_angle_large_positive(self):
        assert normalize_angle(1080) == pytest.approx(0)  # 3 * 360
        assert normalize_angle(990) == pytest.approx(270)  # 2 * 360 + 270

    def test_normalize_angle_large_negative(self):
        assert normalize_angle(-1080) == pytest.approx(0)
        assert normalize_angle(-990) == pytest.approx(90)

    def test_normalize_angle_decimal(self):
        assert normalize_angle(180.5) == pytest.approx(180.5)
        assert normalize_angle(-1.5) == pytest.approx(358.5)


class TestAngleConversions:
    """Test angle conversion utilities."""

    def test_deg_to_rad(self):
        assert deg_to_rad(0) == pytest.approx(0)
        assert deg_to_rad(90) == pytest.approx(math.pi / 2)
        assert deg_to_rad(180) == pytest.approx(math.pi)
        assert deg_to_rad(360) == pytest.approx(2 * math.pi)
        assert deg_to_rad(45) == pytest.approx(math.pi / 4)

    def test_rad_to_deg(self):
        assert rad_to_deg(0) == pytest.approx(0)
        assert rad_to_deg(math.pi / 2) == pytest.approx(90)
        assert rad_to_deg(math.pi) == pytest.approx(180)
        assert rad_to_deg(2 * math.pi) == pytest.approx(360)
        assert rad_to_deg(math.pi / 4) == pytest.approx(45)

    def test_deg_to_rad_roundtrip(self):
        for deg in [0, 45, 90, 180, 270, 360, 123.45]:
            rad = deg_to_rad(deg)
            back_deg = rad_to_deg(rad)
            assert back_deg == pytest.approx(deg, abs=1e-10)

    def test_arcmin_to_deg(self):
        assert arcmin_to_deg(0) == pytest.approx(0)
        assert arcmin_to_deg(60) == pytest.approx(1)
        assert arcmin_to_deg(30) == pytest.approx(0.5)
        assert arcmin_to_deg(120) == pytest.approx(2)

    def test_deg_to_arcmin(self):
        assert deg_to_arcmin(0) == pytest.approx(0)
        assert deg_to_arcmin(1) == pytest.approx(60)
        assert deg_to_arcmin(0.5) == pytest.approx(30)
        assert deg_to_arcmin(2) == pytest.approx(120)

    def test_arcmin_deg_roundtrip(self):
        for arcmin in [0, 30, 60, 90, 120, 180]:
            deg = arcmin_to_deg(arcmin)
            back_arcmin = deg_to_arcmin(deg)
            assert back_arcmin == pytest.approx(arcmin)

    def test_ra_hours_to_deg(self):
        assert ra_hours_to_deg(0) == pytest.approx(0)
        assert ra_hours_to_deg(1) == pytest.approx(15)
        assert ra_hours_to_deg(12) == pytest.approx(180)
        assert ra_hours_to_deg(24) == pytest.approx(360)

    def test_ra_deg_to_hours(self):
        assert ra_deg_to_hours(0) == pytest.approx(0)
        assert ra_deg_to_hours(15) == pytest.approx(1)
        assert ra_deg_to_hours(180) == pytest.approx(12)
        assert ra_deg_to_hours(360) == pytest.approx(24)

    def test_ra_hours_deg_roundtrip(self):
        for hours in [0, 6, 12, 18, 24, 7.5]:
            deg = ra_hours_to_deg(hours)
            back_hours = ra_deg_to_hours(deg)
            assert back_hours == pytest.approx(hours, abs=1e-10)


class TestFovCalculations:
    """Test FOV calculation utilities."""

    def test_calculate_required_fov_no_rotation(self):
        fov = calculate_required_fov(2.0, 1.0, 0, 1.5)
        # Without rotation, need width = 2.0, need height = 1.0
        # Width with margin: 2.0 * 1.06 = 2.12
        # Height with margin * aspect: 1.0 * 1.06 * 1.5 = 1.59
        # Max is 2.12
        assert fov == pytest.approx(2.12, rel=1e-5)

    def test_calculate_required_fov_with_rotation(self):
        fov = calculate_required_fov(2.0, 1.0, 45, 1.5)
        # With 45 degree rotation:
        # need_width = |2*cos45| + |1*sin45| = 1.414 + 0.707 = 2.121
        # need_height = |2*sin45| + |1*cos45| = 1.414 + 0.707 = 2.121
        # Width with margin: 2.121 * 1.06 = 2.248
        # Height with margin * aspect: 2.121 * 1.06 * 1.5 = 3.372
        # Max is 3.372
        assert fov == pytest.approx(3.372, rel=1e-3)

    def test_calculate_required_fov_invalid_input(self):
        assert math.isnan(calculate_required_fov(0, 1, 0, 1.5))
        assert math.isnan(calculate_required_fov(-1, 1, 0, 1.5))
        assert math.isnan(calculate_required_fov(1, 0, 0, 1.5))
        assert math.isnan(calculate_required_fov(math.inf, 1, 0, 1.5))

    def test_calculate_mosaic_dimensions_single_panel(self):
        dims = calculate_mosaic_dimensions(2.0, 1.0, 1, 1, 10)
        assert dims['totalW'] == pytest.approx(2.0)
        assert dims['totalH'] == pytest.approx(1.0)
        assert dims['stepW'] == pytest.approx(1.8)  # 2.0 * (1 - 0.1)
        assert dims['stepH'] == pytest.approx(0.9)  # 1.0 * (1 - 0.1)

    def test_calculate_mosaic_dimensions_2x2(self):
        dims = calculate_mosaic_dimensions(2.0, 1.0, 2, 2, 10)
        assert dims['totalW'] == pytest.approx(3.8)  # 2.0 + 1 * 1.8
        assert dims['totalH'] == pytest.approx(1.9)  # 1.0 + 1 * 0.9
        assert dims['stepW'] == pytest.approx(1.8)
        assert dims['stepH'] == pytest.approx(0.9)

    def test_calculate_mosaic_dimensions_3x3(self):
        dims = calculate_mosaic_dimensions(1.0, 1.0, 3, 3, 20)
        # overlap = 0.2, step = 0.8
        assert dims['totalW'] == pytest.approx(2.6)  # 1.0 + 2 * 0.8
        assert dims['totalH'] == pytest.approx(2.6)  # 1.0 + 2 * 0.8

    def test_calculate_pane_offset_single_panel(self):
        offset = calculate_pane_offset(0, 0, 1, 1, 1.0, 1.0)
        # Center of single panel is at (0, 0)
        assert offset['cx_off'] == pytest.approx(0)
        assert offset['cy_off'] == pytest.approx(0)

    def test_calculate_pane_offset_2x2(self):
        # Bottom-left panel (col=0, row=0)
        bl = calculate_pane_offset(0, 0, 2, 2, 1.0, 1.0)
        assert bl['cx_off'] == pytest.approx(-0.5)  # (0 - 0.5) * 1.0
        assert bl['cy_off'] == pytest.approx(-0.5)  # (0 - 0.5) * 1.0

        # Top-right panel (col=1, row=1)
        tr = calculate_pane_offset(1, 1, 2, 2, 1.0, 1.0)
        assert tr['cx_off'] == pytest.approx(0.5)   # (1 - 0.5) * 1.0
        assert tr['cy_off'] == pytest.approx(0.5)   # (1 - 0.5) * 1.0

    def test_calculate_pane_offset_3x3(self):
        # Center panel (col=1, row=1)
        center = calculate_pane_offset(1, 1, 3, 3, 1.0, 1.0)
        assert center['cx_off'] == pytest.approx(0)
        assert center['cy_off'] == pytest.approx(0)

        # Bottom-left panel (col=0, row=0)
        bl = calculate_pane_offset(0, 0, 3, 3, 1.0, 1.0)
        assert bl['cx_off'] == pytest.approx(-1.0)  # (0 - 1) * 1.0
        assert bl['cy_off'] == pytest.approx(-1.0)  # (0 - 1) * 1.0

    def test_rotate_2d_no_rotation(self):
        result = rotate_2d(1, 0, 0)
        assert result[0] == pytest.approx(1)
        assert result[1] == pytest.approx(0)

    def test_rotate_2d_90_degrees(self):
        result = rotate_2d(1, 0, math.pi / 2)
        assert result[0] == pytest.approx(0)
        assert result[1] == pytest.approx(1)

    def test_rotate_2d_45_degrees(self):
        result = rotate_2d(1, 0, math.pi / 4)
        assert result[0] == pytest.approx(math.sqrt(2) / 2, rel=1e-10)
        assert result[1] == pytest.approx(math.sqrt(2) / 2, rel=1e-10)


class TestMosaicMath:
    """Test mosaic math including spherical stepping."""

    def test_mosaic_3x3_dec_zero(self):
        """Test 3x3 mosaic at celestial equator (Dec=0)."""
        ra_center = 180.0
        dec_center = 0.0
        fov_w = 1.0
        fov_h = 1.0
        cols = 3
        rows = 3
        overlap = 10.0

        # Calculate step sizes
        dims = calculate_mosaic_dimensions(fov_w, fov_h, cols, rows, overlap)
        step_w = dims['stepW']  # 0.9
        step_h = dims['stepH']  # 0.9

        # Calculate offsets for corner panels
        bl = calculate_pane_offset(0, 0, cols, rows, step_w, step_h)
        tr = calculate_pane_offset(2, 2, cols, rows, step_w, step_h)

        # Verify offsets are symmetric
        assert bl['cx_off'] == pytest.approx(-step_w)
        assert bl['cy_off'] == pytest.approx(-step_h)
        assert tr['cx_off'] == pytest.approx(step_w)
        assert tr['cy_off'] == pytest.approx(step_h)

        # Calculate offsets for center panel
        center = calculate_pane_offset(1, 1, cols, rows, step_w, step_h)
        assert center['cx_off'] == pytest.approx(0)
        assert center['cy_off'] == pytest.approx(0)

    def test_mosaic_cosine_correction_factor(self):
        """Test the 1/cos(Dec) factor at various declinations."""
        test_cases = [
            (0.0, 1.0, "At celestial equator, cos(0) = 1"),
            (30.0, 1.1547, "At Dec +30"),
            (45.0, 1.4142, "At Dec +45"),
            (60.0, 2.0, "At Dec +60"),
            (70.0, 2.9238, "At Dec +70"),
            (-30.0, 1.1547, "At Dec -30"),
            (-45.0, 1.4142, "At Dec -45"),
        ]

        for dec_deg, expected_factor, description in test_cases:
            cos_dec = math.cos(deg_to_rad(dec_deg))
            actual_factor = 1.0 / cos_dec
            assert actual_factor == pytest.approx(expected_factor, rel=0.01), description

    def test_mosaic_ra_normalization(self):
        """Test RA coordinate normalization."""
        test_cases = [
            (10.0, -5.0, 5.0),
            (350.0, 20.0, 10.0),
            (5.0, -10.0, 355.0),
            (180.0, 0.0, 180.0),
            (359.9, 0.2, 0.1),
        ]

        for ra_center, offset, expected in test_cases:
            ra_result = ra_center + offset
            ra_normalized = normalize_angle(ra_result)
            assert ra_normalized == pytest.approx(expected, abs=1e-9)


class TestNudgeCalculations:
    """Test nudge/coordinate adjustment calculations."""

    def test_nudge_dec_only(self):
        new_ra, new_dec = calculate_nudged_center(180.0, 45.0, 0, 10)
        assert new_ra == pytest.approx(180.0)
        assert new_dec == pytest.approx(45 + 10/60)  # 10 arcmin = 10/60 degrees

    def test_nudge_ra_only_at_equator(self):
        new_ra, new_dec = calculate_nudged_center(180.0, 0.0, 10, 0)
        # At equator, 10 arcmin West = 10/60 degrees
        assert new_ra == pytest.approx(180 - 10/60)
        assert new_dec == pytest.approx(0.0)

    def test_nudge_ra_only_at_pole(self):
        new_ra, new_dec = calculate_nudged_center(0.0, 89.0, 10, 0)
        # Near pole, RA adjustment is very large due to 1/cos(Dec)
        # At Dec 89, cos(89) ≈ 0.01745, so 10 arcmin ≈ 9.56 degrees RA
        assert new_ra != pytest.approx(0, abs=1)
        assert new_dec == pytest.approx(89.0)

    def test_nudge_ra_wraparound(self):
        new_ra, new_dec = calculate_nudged_center(1.0, 0.0, 120, 0)  # 120 arcmin West
        # Should wrap around to ~359 degrees
        assert new_ra > 350

    def test_nudge_both_coordinates(self):
        new_ra, new_dec = calculate_nudged_center(180.0, 45.0, 10, 10)
        # At Dec 45°, RA adjustment includes 1/cos(45°) factor
        # 10 arcmin = 10/60 degrees, cos(45°) = sqrt(2)/2 ≈ 0.707
        # RA adjustment = (10/60) / cos(45°) ≈ 0.236 degrees
        expected_ra = 180.0 - (10/60) / math.cos(deg_to_rad(45.0))
        assert new_ra == pytest.approx(expected_ra, rel=1e-5)
        assert new_dec == pytest.approx(45 + 10/60)


class TestGeoBeltMath:
    """Test geostationary satellite belt calculations."""

    def test_geo_belt_declination_equator(self):
        dec = calculate_geo_belt_declination(0.0)
        # At equator, sin(0)=0, so parallax is 0, result is 0
        assert dec == pytest.approx(0.0, abs=0.01)

    def test_geo_belt_declination_north_hemisphere(self):
        dec = calculate_geo_belt_declination(45.0)
        # At 45 deg north, geo belt should be negative (around -6.8 degrees)
        assert dec < -6.0
        assert dec > -90

    def test_geo_belt_declination_south_hemisphere(self):
        dec = calculate_geo_belt_declination(-45.0)
        # At 45 deg south, geo belt should be positive (around +6.8 degrees)
        assert dec > 6.0
        assert dec < 90

    def test_geo_belt_declination_north_pole(self):
        dec = calculate_geo_belt_declination(90.0)
        # At north pole (lat=90), geo belt is at about -8.6 degrees
        # atan2(6378, 42164) in radians, converted to degrees
        assert dec == pytest.approx(-8.60, abs=0.01)

    def test_geo_belt_ra_points(self):
        points = generate_geo_belt_ra_points()
        assert len(points) == 360
        assert points[0] == 0
        assert points[359] == 359
        # Points should be monotonically increasing
        for i in range(1, len(points)):
            assert points[i] == points[i-1] + 1


class TestValidation:
    """Test validation utilities."""

    def test_is_valid_coordinate_valid(self):
        assert is_valid_coordinate(180.0, 45.0)
        assert is_valid_coordinate(0.0, 0.0)
        assert is_valid_coordinate(360.0, 90.0)
        assert is_valid_coordinate(0.0, -90.0)

    def test_is_valid_coordinate_invalid_ra(self):
        assert not is_valid_coordinate(-1.0, 45.0)
        assert not is_valid_coordinate(361.0, 45.0)
        assert not is_valid_coordinate(math.nan, 45.0)
        assert not is_valid_coordinate(math.inf, 45.0)

    def test_is_valid_coordinate_invalid_dec(self):
        assert not is_valid_coordinate(180.0, -91.0)
        assert not is_valid_coordinate(180.0, 91.0)
        assert not is_valid_coordinate(180.0, math.nan)
        assert not is_valid_coordinate(180.0, math.inf)

    def test_is_valid_fov_valid(self):
        assert is_valid_fov(1.0, 1.0)
        assert is_valid_fov(0.1, 0.1)
        assert is_valid_fov(180.0, 180.0)

    def test_is_valid_fov_invalid(self):
        assert not is_valid_fov(0.0, 1.0)
        assert not is_valid_fov(1.0, 0.0)
        assert not is_valid_fov(-1.0, 1.0)
        assert not is_valid_fov(1.0, -1.0)
        assert not is_valid_fov(181.0, 1.0)
        assert not is_valid_fov(1.0, 181.0)
        assert not is_valid_fov(math.nan, 1.0)
        assert not is_valid_fov(1.0, math.inf)

    def test_is_valid_rotation_valid(self):
        assert is_valid_rotation(0.0)
        assert is_valid_rotation(180.0)
        assert is_valid_rotation(360.0)

    def test_is_valid_rotation_invalid(self):
        assert not is_valid_rotation(-1.0)
        assert not is_valid_rotation(361.0)
        assert not is_valid_rotation(math.nan)
        assert not is_valid_rotation(math.inf)


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_normalize_angle_extreme(self):
        assert normalize_angle(1e10) == pytest.approx(normalize_angle(1e10 % 360))

    def test_fov_calculation_extreme_rotation(self):
        fov = calculate_required_fov(2.0, 1.0, 90, 1.5)
        # At 90 deg rotation, width and height swap
        # need_width = |2*cos90| + |1*sin90| = 0 + 1 = 1
        # need_height = |2*sin90| + |1*cos90| = 2 + 0 = 2
        # Width with margin: 1.0 * 1.06 = 1.06
        # Height with margin * aspect: 2.0 * 1.06 * 1.5 = 3.18
        # Max is 3.18
        assert fov == pytest.approx(3.18, rel=1e-3)

    def test_fov_calculation_square_fov(self):
        fov = calculate_required_fov(1.0, 1.0, 45, 1.5)
        # Square FOV, rotation shouldn't change bounding box size
        # need_width = need_height = |1*cos45| + |1*sin45| = 0.707 + 0.707 = 1.414
        # With margin and aspect: 1.414 * 1.06 * 1.5 = 2.248
        assert fov == pytest.approx(2.248, rel=1e-3)

    def test_mosaic_zero_overlap(self):
        dims = calculate_mosaic_dimensions(1.0, 1.0, 2, 2, 0)
        assert dims['totalW'] == pytest.approx(2.0)  # 1.0 + 1 * 1.0
        assert dims['totalH'] == pytest.approx(2.0)  # 1.0 + 1 * 1.0

    def test_mosaic_100_percent_overlap(self):
        dims = calculate_mosaic_dimensions(1.0, 1.0, 2, 2, 100)
        assert dims['totalW'] == pytest.approx(1.0)  # All panels at same position
        assert dims['totalH'] == pytest.approx(1.0)

    def test_rotate_2d_negative_angle(self):
        result = rotate_2d(1, 0, -math.pi / 2)
        assert result[0] == pytest.approx(0)
        assert result[1] == pytest.approx(-1)

    def test_rotate_2d_roundtrip(self):
        x, y = 1.5, 2.3
        rotated = rotate_2d(x, y, math.pi / 3)
        rotated_back = rotate_2d(rotated[0], rotated[1], -math.pi / 3)
        assert rotated_back[0] == pytest.approx(x, rel=1e-10)
        assert rotated_back[1] == pytest.approx(y, rel=1e-10)

    def test_nudge_small_offsets(self):
        # Very small nudges (less than 1 arcmin)
        new_ra, new_dec = calculate_nudged_center(180.0, 45.0, 0.5, 0.5)
        assert new_ra < 180.0
        assert new_dec > 45.0
        # Check changes are small
        assert 180.0 - new_ra < 1
        assert new_dec - 45.0 < 1

    def test_nudge_large_offsets(self):
        # Very large nudges (several degrees)
        new_ra, new_dec = calculate_nudged_center(180.0, 0.0, 720, 720)  # 12 deg
        assert new_ra < 180.0
        assert new_dec == pytest.approx(12.0)
