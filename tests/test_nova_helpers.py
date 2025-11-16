# In tests/test_nova_helpers.py
from nova import normalize_object_name, get_user_log_string
from modules.astro_calculations import hms_to_hours
import pytest # Import pytest to use its features


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
    assert hms_to_hours("12.5") == 12.5 # Handles decimal input


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
    (4, "JustAName", "(4 | JustAName)"),          # <-- This is the corrected line
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