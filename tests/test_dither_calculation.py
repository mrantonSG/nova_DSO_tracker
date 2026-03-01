"""
Tests for dither display functionality.

Tests the dither_display() helper function which formats structured dither
fields for display, with legacy fallback to dither_details.
"""

import pytest
from nova.helpers import dither_display


class MockSession:
    """Mock JournalSession object for testing."""
    def __init__(
        self,
        dither_pixels=None,
        dither_every_n=None,
        dither_notes=None,
        dither_details=None
    ):
        self.dither_pixels = dither_pixels
        self.dither_every_n = dither_every_n
        self.dither_notes = dither_notes
        self.dither_details = dither_details


def test_dither_display_pixels_only():
    """Test dither display with only pixels set."""
    session = MockSession(dither_pixels=7)
    assert dither_display(session) == "7 px"


def test_dither_display_pixels_every_one_sub():
    """Test dither display with pixels and every_n=1."""
    session = MockSession(dither_pixels=7, dither_every_n=1)
    assert dither_display(session) == "7 px, every sub"


def test_dither_display_pixels_every_n_subs():
    """Test dither display with pixels and every_n > 1."""
    session = MockSession(dither_pixels=7, dither_every_n=3)
    assert dither_display(session) == "7 px, every 3 subs"


def test_dither_display_with_notes():
    """Test dither display with all structured fields."""
    session = MockSession(
        dither_pixels=7,
        dither_every_n=3,
        dither_notes="disabled for Ha"
    )
    assert dither_display(session) == "7 px, every 3 subs (disabled for Ha)"


def test_dither_display_legacy_fallback():
    """Test dither display falls back to legacy dither_details."""
    session = MockSession(
        dither_pixels=None,
        dither_details="Yes, 3px every 2 subs"
    )
    assert dither_display(session) == "Yes, 3px every 2 subs"


def test_dither_display_all_none():
    """Test dither display returns empty string when all fields are None."""
    session = MockSession()
    assert dither_display(session) == ""


def test_dither_display_pixels_only_no_notes():
    """Test dither display with pixels only, no notes."""
    session = MockSession(dither_pixels=3, dither_notes=None)
    assert dither_display(session) == "3 px"


def test_dither_display_pixels_and_notes_no_every_n():
    """Test dither display with pixels and notes, but no every_n."""
    session = MockSession(
        dither_pixels=5,
        dither_every_n=None,
        dither_notes="random pattern"
    )
    assert dither_display(session) == "5 px (random pattern)"


def test_dither_display_empty_notes():
    """Test dither display ignores empty notes."""
    session = MockSession(
        dither_pixels=7,
        dither_every_n=2,
        dither_notes=""
    )
    assert dither_display(session) == "7 px, every 2 subs"


def test_dither_display_legacy_empty_string():
    """Test dither display handles empty legacy string."""
    session = MockSession(dither_pixels=None, dither_details="")
    assert dither_display(session) == ""


def test_dither_display_large_pixels():
    """Test dither display with large pixel values."""
    session = MockSession(dither_pixels=50, dither_every_n=1)
    assert dither_display(session) == "50 px, every sub"


def test_dither_display_large_every_n():
    """Test dither display with large every_n values."""
    session = MockSession(dither_pixels=7, dither_every_n=99)
    assert dither_display(session) == "7 px, every 99 subs"


def test_dither_display_zero_pixels():
    """Test dither display handles zero pixels (edge case)."""
    session = MockSession(dither_pixels=0, dither_every_n=1)
    assert dither_display(session) == "0 px, every sub"


def test_dither_display_structured_preferred_over_legacy():
    """Test that structured fields are preferred even when legacy is set."""
    session = MockSession(
        dither_pixels=7,
        dither_every_n=2,
        dither_details="Yes, 3px every 2 subs"
    )
    # Should use structured fields, ignore legacy
    assert dither_display(session) == "7 px, every 2 subs"
