# In tests/test_nova_helpers.py
from nova import normalize_object_name, get_user_log_string
from modules.astro_calculations import hms_to_hours
import pytest  # Import pytest to use its features

# --- Imports needed for new tests ---
import sys, os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from nova import Component, _compute_rig_metrics_from_components


# --- End new imports ---


def test_hms_to_hours_with_seconds():
    assert hms_to_hours("01:00:36") == 1.01


def test_hms_to_hours_simple():
    assert hms_to_hours("12:30:00") == 12.5
    assert hms_to_hours("06:00:00") == 6.0


def test_hms_to_hours_no_seconds():
    assert hms_to_hours("10:15") == 10.25


def test_hms_to_hours_handles_bad_input():
    # Test that it doesn't crash on bad data
    assert hms_to_hours("abc:def") == 0.0
    assert hms_to_hours(None) == 0.0
    assert hms_to_hours("12.5") == 12.5  # Handles decimal input


# --- Test Function 2: normalize_object_name ---
# We can use @pytest.mark.parametrize to run many tests in one function
@pytest.mark.parametrize("corrupt_input, expected_output", [
    ("M 42", "M42"),
    ("m 42", "M42"),
    ("SH2129", "SH 2-129"),
    ("NGC1976", "NGC 1976"),
    ("IC 405", "IC 405"),
    (" LHA 120-N 70 ", "LHA 120-N 70"),
    ("M31", "M31"),
])
def test_normalize_object_name(corrupt_input, expected_output):
    assert normalize_object_name(corrupt_input) == expected_output


# --- Test Function 3: get_user_log_string ---
@pytest.mark.parametrize("user_id, username, expected_output", [
    (1, "mrantonSG", "(1 | mrantonSG)"),
    (2, "Test User", "(2 | Test U.)"),
    (3, "Jane van der Beek", "(3 | Jane B.)"),
    (4, "JustAName", "(4 | JustAName)"),
    (5, None, "(5 | unknown)"),
    (None, "Test User", "(None | Test U.)"),
    (7, " ", "(7 | unknown)"),
    (8, "  ", "(8 | unknown)"),
    (9, "Paul", "(9 | Paul)"),
])
def test_get_user_log_string(user_id, username, expected_output):
    """
    Tests the new privacy-aware log string generator.
    """
    assert get_user_log_string(user_id, username) == expected_output


# --- NEW TESTS FOR RIG METRICS ---
@pytest.fixture
def test_components():
    """Provides a set of test components for rig calculations."""
    scope = Component(
        name="Test Scope",
        focal_length_mm=400,
        aperture_mm=80
    )
    cam = Component(
        name="Test Camera",
        pixel_size_um=3.75,
        sensor_width_mm=17.5,
        sensor_height_mm=13.0
    )
    reducer = Component(
        name="Test Reducer",
        factor=0.8
    )
    extender = Component(
        name="Test Extender",
        factor=2.0
    )
    return {
        "scope": scope,
        "cam": cam,
        "reducer": reducer,
        "extender": extender
    }


def test_rig_metrics_scope_and_cam_only(test_components):
    """Tests calculation with just a telescope and camera."""
    scope = test_components["scope"]
    cam = test_components["cam"]

    efl, f_ratio, scale, fov_w = _compute_rig_metrics_from_components(scope, cam, None)

    assert efl == 400.0  # 400 * 1.0
    assert f_ratio == 5.0  # 400 / 80
    assert scale == pytest.approx(1.933, abs=0.001)  # (206.265 * 3.75) / 400
    assert fov_w == pytest.approx(150.3, abs=0.1)  # fov for 17.5mm sensor at 400mm fl


def test_rig_metrics_with_reducer(test_components):
    """Tests calculation with a reducer."""
    scope = test_components["scope"]
    cam = test_components["cam"]
    reducer = test_components["reducer"]

    efl, f_ratio, scale, fov_w = _compute_rig_metrics_from_components(scope, cam, reducer)

    assert efl == 320.0  # 400 * 0.8
    assert f_ratio == 4.0  # 320 / 80
    assert scale == pytest.approx(2.417, abs=0.001)  # (206.265 * 3.75) / 320
    # --- THIS LINE IS FIXED ---
    assert fov_w == pytest.approx(187.95, abs=0.1)  # fov for 17.5mm sensor at 320mm fl


def test_rig_metrics_with_extender(test_components):
    """Tests calculation with an extender/barlow."""
    scope = test_components["scope"]
    cam = test_components["cam"]
    extender = test_components["extender"]

    efl, f_ratio, scale, fov_w = _compute_rig_metrics_from_components(scope, cam, extender)

    assert efl == 800.0  # 400 * 2.0
    assert f_ratio == 10.0  # 800 / 80
    assert scale == pytest.approx(0.966, abs=0.001)  # (206.265 * 3.75) / 800
    # --- THIS LINE IS FIXED ---
    assert fov_w == pytest.approx(75.20, abs=0.1)  # fov for 17.5mm sensor at 800mm fl


def test_rig_metrics_missing_data(test_components):
    """Tests that it handles missing components gracefully."""
    scope = test_components["scope"]
    cam = test_components["cam"]

    # Missing telescope
    efl, f_ratio, scale, fov_w = _compute_rig_metrics_from_components(None, cam, None)
    assert (efl, f_ratio, scale, fov_w) == (None, None, None, None)

    # Missing camera
    efl, f_ratio, scale, fov_w = _compute_rig_metrics_from_components(scope, None, None)
    assert (efl, f_ratio, scale, fov_w) == (None, None, None, None)

    # Missing critical scope data
    scope_no_fl = Component(name="No FL", aperture_mm=80)
    efl, f_ratio, scale, fov_w = _compute_rig_metrics_from_components(scope_no_fl, cam, None)
    assert (efl, f_ratio, scale, fov_w) == (None, None, None, None)