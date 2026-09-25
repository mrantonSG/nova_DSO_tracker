"""Unit tests for resolve_altitude_threshold: location override, else global, else 20."""

import pytest

from nova.helpers import resolve_altitude_threshold
from nova.models import Location


def _row(threshold):
    return Location(name="Row", lat=50.0, lon=10.0, timezone="UTC", altitude_threshold=threshold)


@pytest.mark.parametrize("location", [
    {"altitude_threshold": 35},
    _row(35),
], ids=["dict", "orm"])
def test_location_override_beats_global(location):
    assert resolve_altitude_threshold({"altitude_threshold": 20}, location) == 35


@pytest.mark.parametrize("location", [
    {"altitude_threshold": 0},
    _row(0),
], ids=["dict", "orm"])
def test_location_zero_beats_global(location):
    assert resolve_altitude_threshold({"altitude_threshold": 30}, location) == 0


@pytest.mark.parametrize("location", [
    {"altitude_threshold": None},
    {},
    _row(None),
    None,
], ids=["dict-none", "dict-missing", "orm-none", "no-location"])
def test_location_none_falls_through_to_global(location):
    assert resolve_altitude_threshold({"altitude_threshold": 30}, location) == 30


@pytest.mark.parametrize("user_config", [
    {"altitude_threshold": None},
    {},
    None,
], ids=["global-none", "global-missing", "no-config"])
@pytest.mark.parametrize("location", [
    {"altitude_threshold": None},
    _row(None),
    None,
], ids=["dict-none", "orm-none", "no-location"])
def test_global_none_gives_20(user_config, location):
    assert resolve_altitude_threshold(user_config, location) == 20


def test_global_zero_is_kept():
    assert resolve_altitude_threshold({"altitude_threshold": 0}, None) == 0
