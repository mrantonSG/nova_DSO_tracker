"""
Unit tests for the guiding_rms_band() helper in nova/helpers.py.

The helper mirrors updateGuidingRmsHint() in static/js/journal_section.js:
limits are the effective scale x GUIDING_RMS_FACTORS (image scale floored at
1.0 arcsec/px), rounded to 2 decimals, compared inclusively (rms <= limit).
"""

import pytest

from nova.helpers import guiding_rms_band


# 1.44 x (1/3, 0.5, 1.0, 1.5) -> rounded limits 0.48 / 0.72 / 1.44 / 2.16
SCALE = 1.44


@pytest.mark.parametrize(
    ("rms", "expected"),
    [
        (0.48, "excellent"),    # exactly at the 1/3-scale limit (inclusive)
        (0.49, "good"),
        (0.72, "good"),         # exactly at the 1/2-scale limit (inclusive)
        (0.73, "acceptable"),
        (1.44, "acceptable"),   # exactly at the 1x-scale limit (inclusive)
        (2.16, "borderline"),   # exactly at the 1.5x-scale limit (inclusive)
        (2.17, "unusable"),     # above the 1.5x-scale limit
    ],
)
def test_scale_144(rms, expected):
    assert guiding_rms_band(rms, SCALE) == expected


@pytest.mark.parametrize(
    ("rms", "scale"),
    [
        (None, SCALE),           # rms missing
        (0.5, None),             # scale missing
        (None, None),            # both missing
        (0, SCALE),              # rms zero
        (0.5, 0),                # scale zero
        (-0.5, SCALE),           # rms negative
        (0.5, -1.44),            # scale negative
        ("abc", SCALE),          # rms non-numeric
        (0.5, "abc"),            # scale non-numeric
        (float("nan"), SCALE),   # rms not finite
        (0.5, float("inf")),     # scale not finite
    ],
)
def test_missing_zero_negative_non_numeric_returns_none(rms, scale):
    assert guiding_rms_band(rms, scale) is None


# 0.39 is below the 1.0 minimum scale, so limits are computed from 1.0 ->
# rounded limits 0.33 / 0.50 / 1.00 / 1.50
FINE_SCALE = 0.39


@pytest.mark.parametrize(
    ("rms", "expected"),
    [
        (0.33, "excellent"),    # exactly at the floored 1/3 limit (inclusive)
        (0.34, "good"),
        (0.50, "good"),         # exactly at the floored 1/2 limit (inclusive)
        (0.65, "acceptable"),
        (1.00, "acceptable"),   # exactly at the floored 1x limit (inclusive)
        (1.50, "borderline"),   # exactly at the floored 1.5x limit (inclusive)
        (1.51, "unusable"),     # above the floored 1.5x limit
    ],
)
def test_scale_below_minimum_uses_minimum(rms, expected):
    assert guiding_rms_band(rms, FINE_SCALE) == expected
