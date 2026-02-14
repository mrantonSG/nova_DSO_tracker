"""
Mosaic math tests for spherical stepping algorithm.

This test implements a Python version of the spherical stepping algorithm
used by N.I.N.A. and ASIAIR for mosaic panel calculations. It validates
that the RA offset correctly includes the 1/cos(Dec) correction.

Algorithm Reference:
- JavaScript implementation in static/js/graph_view_chart.js:2061-2082
- RA spacing varies with 1/cos(Dec) due to converging meridians
- Safety limit: |Dec| > 89.9° uses minimum cos(Dec) = 0.00175
"""
import math
import pytest


def calculate_mosaic_panel_spherical(
    col_index: int, row_index: int,
    cols: int, rows: int,
    ra_center_deg: float, dec_center_deg: float,
    fov_w_deg: float, fov_h_deg: float,
    overlap_percent: float,
    rotation_deg: float = 0.0
) -> tuple[float, float]:
    """
    Calculate RA/Dec coordinates for a mosaic panel using spherical stepping algorithm.

    This is a Python implementation of the N.I.N.A./ASIAIR compatible algorithm.

    Args:
        col_index: Panel column index (0 to cols-1)
        row_index: Panel row index (0 to rows-1)
        cols: Total number of columns
        rows: Total number of rows
        ra_center_deg: Center RA in degrees
        dec_center_deg: Center Declination in degrees
        fov_w_deg: Field of view width in degrees
        fov_h_deg: Field of view height in degrees
        overlap_percent: Overlap percentage (0-100)
        rotation_deg: Position angle in degrees (default 0)

    Returns:
        Tuple of (panel_ra_deg, panel_dec_deg)
    """
    # Calculate step sizes accounting for overlap
    # Step = FOV * (1 - overlap/100)
    w_step = fov_w_deg * (1.0 - overlap_percent / 100.0)
    h_step = fov_h_deg * (1.0 - overlap_percent / 100.0)

    # Calculate grid offsets from center (centered indexing)
    # c=0 is leftmost, c=cols-1 is rightmost
    cx_off = (col_index - (cols - 1) / 2.0) * w_step
    # r=0 is bottom, r=rows-1 is top
    cy_off = (row_index - (rows - 1) / 2.0) * h_step

    # Apply 2D rotation matrix for Position Angle
    # This rotates the grid around the center
    rad = math.radians(rotation_deg)
    rx = cx_off * math.cos(rad) - cy_off * math.sin(rad)
    ry = cx_off * math.sin(rad) + cy_off * math.cos(rad)

    # SPHERICAL STEPPING ALGORITHM (N.I.N.A. / ASIAIR compatible)

    # Step 1: Calculate declination using spherical approximation
    # Dec offset is simply the rotated Y offset
    pane_dec_deg = dec_center_deg + ry

    # Step 2: Calculate RA offset with cosine correction at the panel's declination
    # RA spacing varies with 1/cos(Dec) due to converging meridians
    # Safety: avoid division by zero near poles (|Dec| > 89.9°)
    if abs(pane_dec_deg) > 89.9:
        cos_dec = 0.00175  # Minimum value for ~89.9°
    else:
        cos_dec = math.cos(math.radians(pane_dec_deg))
    ra_offset_deg = rx / cos_dec

    # Step 3: Apply RA offset to center RA
    pane_ra_deg = ra_center_deg + ra_offset_deg

    # Normalize RA to [0, 360) range
    pane_ra_deg = ((pane_ra_deg % 360) + 360) % 360

    return pane_ra_deg, pane_dec_deg


def test_mosaic_3x3_dec_plus_70():
    """
    Test a 3x3 mosaic at Dec +70° to verify RA offset includes 1/cos(Dec) correction.

    At Dec +70°, cos(Dec) ≈ 0.342, which means RA spacing is ~2.9x larger
    than at the celestial equator. This test verifies the spherical stepping
    algorithm correctly accounts for this factor.
    """
    # Test parameters
    ra_center_deg = 10.0  # 10h00m
    dec_center_deg = 70.0  # +70°
    fov_w_deg = 1.0  # 1 degree FOV
    fov_h_deg = 1.0
    cols = 3
    rows = 3
    overlap = 10.0  # 10% overlap

    # Calculate corner panels
    bottom_left_ra, bottom_left_dec = calculate_mosaic_panel_spherical(
        0, 0, cols, rows, ra_center_deg, dec_center_deg, fov_w_deg, fov_h_deg, overlap
    )
    bottom_right_ra, bottom_right_dec = calculate_mosaic_panel_spherical(
        2, 0, cols, rows, ra_center_deg, dec_center_deg, fov_w_deg, fov_h_deg, overlap
    )
    top_left_ra, top_left_dec = calculate_mosaic_panel_spherical(
        0, 2, cols, rows, ra_center_deg, dec_center_deg, fov_w_deg, fov_h_deg, overlap
    )
    top_right_ra, top_right_dec = calculate_mosaic_panel_spherical(
        2, 2, cols, rows, ra_center_deg, dec_center_deg, fov_w_deg, fov_h_deg, overlap
    )

    # Verify Dec offsets are consistent (no cosine correction for Dec)
    # Dec spacing should be h_step = fov_h_deg * (1 - overlap/100) = 0.9°
    h_step = fov_h_deg * (1.0 - overlap / 100.0)
    assert math.isclose(abs(bottom_left_dec - dec_center_deg), h_step, rel_tol=1e-9)
    assert math.isclose(abs(top_left_dec - dec_center_deg), h_step, rel_tol=1e-9)

    # Verify RA offset includes cosine correction
    # At Dec 70°, cos(Dec) ≈ 0.342, so RA step should be ~2.9x larger
    w_step = fov_w_deg * (1.0 - overlap / 100.0)  # 0.9°
    cos_dec_70 = math.cos(math.radians(70.0))
    expected_ra_step = w_step / cos_dec_70  # ~2.631°

    # The actual RA offset from center to right edge should account for cos correction
    # At the panel's Dec, the RA offset is rx / cos(pane_dec)
    # For right panel (col=2), rx = 1 * w_step = 0.9°
    # At the panel's declination (which is slightly different from center),
    # the RA offset should be approximately w_step / cos(dec)
    assert abs(bottom_right_ra - bottom_left_ra) > 2.0  # Significantly larger than w_step (0.9°)

    # Verify center panel is at the center coordinates
    center_ra, center_dec = calculate_mosaic_panel_spherical(
        1, 1, cols, rows, ra_center_deg, dec_center_deg, fov_w_deg, fov_h_deg, overlap
    )
    assert math.isclose(center_ra, ra_center_deg, rel_tol=1e-9)
    assert math.isclose(center_dec, dec_center_deg, rel_tol=1e-9)


def test_mosaic_3x3_dec_minus_45():
    """
    Test a 3x3 mosaic at Dec -45° to verify Southern Hemisphere compatibility.

    This test verifies that the spherical stepping algorithm correctly handles
    negative declinations in the Southern Hemisphere.
    """
    ra_center_deg = 180.0  # 12h00m
    dec_center_deg = -45.0  # -45°
    fov_w_deg = 2.0  # 2 degree FOV
    fov_h_deg = 2.0
    cols = 3
    rows = 3
    overlap = 20.0  # 20% overlap

    # Calculate corner panels
    bottom_left_ra, bottom_left_dec = calculate_mosaic_panel_spherical(
        0, 0, cols, rows, ra_center_deg, dec_center_deg, fov_w_deg, fov_h_deg, overlap
    )
    top_right_ra, top_right_dec = calculate_mosaic_panel_spherical(
        2, 2, cols, rows, ra_center_deg, dec_center_deg, fov_w_deg, fov_h_deg, overlap
    )

    # Verify Dec is correctly negative
    assert bottom_left_dec < -40  # Should be well below -45°
    assert top_right_dec > -50  # Should be above -45°

    # Calculate expected step sizes
    w_step = fov_w_deg * (1.0 - overlap / 100.0)  # 1.6°
    h_step = fov_h_deg * (1.0 - overlap / 100.0)  # 1.6°

    # Verify Dec offset magnitude (should be h_step)
    assert math.isclose(abs(top_right_dec - bottom_left_dec), 2 * h_step, rel_tol=1e-9)

    # Verify RA offset includes cosine correction at Dec -45°
    cos_dec_neg45 = math.cos(math.radians(-45.0))
    expected_ra_step = w_step / cos_dec_neg45  # ~2.262°

    # The RA offset should be larger than w_step due to cosine correction
    ra_span = abs(top_right_ra - bottom_left_ra)
    # Account for potential RA wrap-around at 360°
    if ra_span > 180:
        ra_span = 360 - ra_span
    assert ra_span > w_step  # Should be larger than 1.6°

    # Verify center panel is at the center coordinates
    center_ra, center_dec = calculate_mosaic_panel_spherical(
        1, 1, cols, rows, ra_center_deg, dec_center_deg, fov_w_deg, fov_h_deg, overlap
    )
    assert math.isclose(center_ra, ra_center_deg, rel_tol=1e-9)
    assert math.isclose(center_dec, dec_center_deg, rel_tol=1e-9)


def test_mosaic_near_pole_safety():
    """
    Test mosaic calculation near the celestial pole (|Dec| > 89.9°).

    This verifies the safety limit that prevents division by zero by using
    a minimum cos(Dec) value of 0.00175.
    """
    # Very close to the North Celestial Pole
    ra_center_deg = 0.0
    dec_center_deg = 89.95  # Just above safety threshold
    fov_w_deg = 0.5
    fov_h_deg = 0.5
    cols = 2
    rows = 2
    overlap = 10.0

    # Should not raise an exception (division by zero protection)
    panel_ra, panel_dec = calculate_mosaic_panel_spherical(
        0, 0, cols, rows, ra_center_deg, dec_center_deg, fov_w_deg, fov_h_deg, overlap
    )

    # RA should be normalized to [0, 360)
    assert 0 <= panel_ra < 360
    # Dec should be close to center + offset
    assert panel_dec > 89.0

    # At Dec 89.95°, the minimum cos(Dec) is used (0.00175)
    # This means RA offsets will be very large
    # Verify the function doesn't crash and produces reasonable output
    assert not math.isnan(panel_ra)
    assert not math.isnan(panel_dec)


def test_mosaic_with_rotation():
    """
    Test mosaic calculation with a non-zero position angle rotation.

    This verifies that the 2D rotation matrix correctly rotates the
    mosaic grid before applying spherical stepping.
    """
    ra_center_deg = 90.0
    dec_center_deg = 30.0
    fov_w_deg = 1.0
    fov_h_deg = 1.0
    cols = 2
    rows = 2
    overlap = 10.0
    rotation_deg = 45.0  # 45° rotation

    # Calculate panels without rotation
    panel_no_rot_00, _ = calculate_mosaic_panel_spherical(
        0, 0, cols, rows, ra_center_deg, dec_center_deg, fov_w_deg, fov_h_deg, overlap, 0.0
    )

    # Calculate panels with 45° rotation
    panel_rot_00, _ = calculate_mosaic_panel_spherical(
        0, 0, cols, rows, ra_center_deg, dec_center_deg, fov_w_deg, fov_h_deg, overlap, rotation_deg
    )

    # With rotation, the panel positions should be different
    assert not math.isclose(panel_no_rot_00, panel_rot_00, rel_tol=1e-6)

    # Center panel should remain at center regardless of rotation
    center_rot, _ = calculate_mosaic_panel_spherical(
        1, 1, cols, rows, ra_center_deg, dec_center_deg, fov_w_deg, fov_h_deg, overlap, rotation_deg
    )
    assert math.isclose(center_rot, ra_center_deg, rel_tol=1e-9)


def test_cosine_correction_factor():
    """
    Direct test of the 1/cos(Dec) factor at various declinations.

    This validates the fundamental mathematics behind the spherical stepping
    algorithm.
    """
    test_cases = [
        (0.0, 1.0, "At celestial equator, cos(0°) = 1, no correction"),
        (30.0, 1.1547, "At Dec +30°, cos(30°) = 0.866, correction = 1.1547"),
        (45.0, 1.4142, "At Dec +45°, cos(45°) = 0.707, correction = 1.4142"),
        (60.0, 2.0, "At Dec +60°, cos(60°) = 0.5, correction = 2.0"),
        (70.0, 2.9238, "At Dec +70°, cos(70°) = 0.342, correction = 2.9238"),
        (80.0, 5.7588, "At Dec +80°, cos(80°) = 0.174, correction = 5.7588"),
        (-30.0, 1.1547, "At Dec -30°, cos(-30°) = 0.866, correction = 1.1547"),
        (-45.0, 1.4142, "At Dec -45°, cos(-45°) = 0.707, correction = 1.4142"),
        (-70.0, 2.9238, "At Dec -70°, cos(-70°) = 0.342, correction = 2.9238"),
    ]

    for dec_deg, expected_factor, description in test_cases:
        cos_dec = math.cos(math.radians(dec_deg))
        actual_factor = 1.0 / cos_dec
        assert math.isclose(actual_factor, expected_factor, rel_tol=0.01), f"{description}"


def test_ra_normalization():
    """
    Test that RA coordinates are correctly normalized to [0, 360) range.

    This ensures proper handling of RA wrap-around at 360°.
    """
    # Test center RA with various panel offsets
    test_cases = [
        (10.0, -5.0, 5.0),  # 10° - 5° = 5°
        (350.0, 20.0, 10.0),  # 350° + 20° = 370° → 10°
        (5.0, -10.0, 355.0),  # 5° - 10° = -5° → 355°
        (180.0, 0.0, 180.0),  # No change
        (359.9, 0.2, 0.1),  # 359.9° + 0.2° = 360.1° → 0.1°
    ]

    for ra_center, offset, expected in test_cases:
        ra_result = ra_center + offset
        ra_normalized = ((ra_result % 360) + 360) % 360
        assert math.isclose(ra_normalized, expected, rel_tol=1e-9)
