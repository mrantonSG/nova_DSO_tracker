"""Tests for update_outlook_cache() called directly (not from a background thread).

Verifies:
  - JSON cache file is written to disk with valid structure
  - metadata + opportunities keys are present
  - cache_worker_status is set to "complete" (not "error")
  - Empty-opportunities path when no active projects exist

All external astronomy libraries (ephem, astropy) are mocked so tests
run offline and instantly.  The DB uses the same in-memory SQLite schema
as production via the conftest.py patterns.

Import note: calculate_observable_duration_vectorized is imported in
nova/__init__.py at line 79 via `from modules.astro_calculations import ...`.
The module is registered in sys.modules as 'nova', so the monkeypatch target
is nova.calculate_observable_duration_vectorized.
"""

import json
import os
from datetime import timedelta, time as dt_time

from datetime import datetime
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def outlook_test_db(monkeypatch):
    """Set up an in-memory DB with a user, location, and one active project (M31)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker, scoped_session

    engine = create_engine("sqlite:///:memory:")
    from nova import Base, get_or_create_db_user, DbUser, Location, AstroObject

    Base.metadata.create_all(engine)
    TestSessionLocal = scoped_session(
        sessionmaker(autocommit=False, autoflush=False, bind=engine)
    )
    monkeypatch.setattr("nova.SessionLocal", TestSessionLocal)
    monkeypatch.setattr("nova.helpers.SessionLocal", TestSessionLocal)
    monkeypatch.setattr("nova.get_db", TestSessionLocal)
    monkeypatch.setattr(TestSessionLocal, "remove", lambda: None)

    session = TestSessionLocal()
    user = get_or_create_db_user(session, "default")

    location = Location(
        user_id=user.id,
        name="Bad Fischau",
        lat=47.83,
        lon=16.17,
        timezone="Europe/Vienna",
        is_default=True,
    )
    session.add(location)

    m31 = AstroObject(
        user_id=user.id,
        object_name="M31",
        common_name="Andromeda Galaxy",
        ra_hours=10.68,
        dec_deg=41.27,
        active_project=True,
        project_name="M31",
    )
    session.add(m31)
    session.commit()

    try:
        yield {
            "session": session,
            "user_id": user.id,
            "location_name": "Bad Fischau",
        }
    finally:
        from nova.config import (
            observable_objects_cache,
            nightly_curves_cache,
            astro_context_cache,
        )

        observable_objects_cache.clear()
        nightly_curves_cache.clear()
        astro_context_cache.clear()
        try:
            session.rollback()
        except Exception:
            pass
        TestSessionLocal.remove()
        Base.metadata.drop_all(engine)


# ---------------------------------------------------------------------------
# Helpers — return values that match production signatures
# ---------------------------------------------------------------------------

def _make_mock_duration(duration_minutes=180, max_altitude=65.0):
    """Return a tuple matching calculate_observable_duration_vectorized()."""
    return (timedelta(minutes=duration_minutes), max_altitude, dt_time(21, 0), dt_time(3, 0))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestUpdateOutlookCacheDirect:
    """Test update_outlook_cache() called directly with mocked astronomy."""

    @pytest.fixture(autouse=True)
    def _patch_astronomy(self, monkeypatch):
        """Mock external astronomy so update_outlook_cache runs offline and fast.

        Strategy:
          - ephem.Moon → 5% phase (dark sky)
          - calculate_observable_duration_vectorized → 180min / 65° (passes default criteria)
          - calculate_sun_events_cached → fixed dusk/dawn times (avoids real solar math)
          - astropy coordinate constructors → stubs that accept REAL units (so
            calculate_observable_duration_vectorized's internal unit math works)
              and return objects passing every attribute access in the separation
              block (lines 1910-1926 of nova/__init__.py).

        Patch targets are nova.* because the separation block looks up
        EarthLocation, Time, AltAz, SkyCoord, get_body from the nova module.
        """

        # --- ephem.Moon: 5% phase (dark sky) --------------------------------
        _moon = MagicMock()
        _moon.phase = 5.0
        monkeypatch.setattr("ephem.Moon", lambda *a, **k: _moon)

        # --- astropy IERS config (prevents auto-download on import) ----------
        _mock_iers = MagicMock()
        _mock_iers.conf = MagicMock(auto_download=False, auto_max_age=None)
        monkeypatch.setattr("astropy.utils.iers", _mock_iers)

        # --- calculate_sun_events_cached: return fixed solar times -----------
        monkeypatch.setattr(
            "nova.calculate_sun_events_cached",
            lambda *a, **k: {
                "astronomical_dusk": "22:00",
                "astronomical_dawn": "03:00",
                "sunset": "21:00",
                "sunrise": "05:00",
                "transit": "12:00",
            },
        )

        # --- calculate_observable_duration_vectorized — 180min, 65° max alt --
        # Patch where it is USED (imported at top of nova/__init__.py, line 79).
        # The module is registered in sys.modules as 'nova', so the patch target
        # must be 'nova.calculate_observable_duration_vectorized'.
        monkeypatch.setattr(
            "nova.calculate_observable_duration_vectorized",
            lambda *a, **k: _make_mock_duration(),
        )

        # --- astropy coordinate stubs (patched on nova.* namespace) ----------
        # These receive REAL astropy units from calculate_observable_duration_vectorized's
        # internal calls, so we keep the real astropy.units module for unit math.

        class _FakeEarthLoc:
            """Stub replacing nova.EarthLocation."""

            def __init__(self, *args, **kwargs):
                pass

        monkeypatch.setattr("nova.EarthLocation", _FakeEarthLoc)

        class _FakeTime:
            """Stub replacing nova.Time."""

            shape = None

            def __init__(self, *a, **k):
                pass

            def astimezone(self, tz):
                return datetime(2026, 6, 1, 12, 0, tzinfo=tz)

        monkeypatch.setattr("nova.Time", _FakeTime)

        class _FakeAltAz:
            """Stub replacing nova.AltAz."""

            def __init__(self, **kwargs):
                pass

        monkeypatch.setattr("nova.AltAz", _FakeAltAz)

        class _FakeSkyCoord:
            """Stub replacing nova.SkyCoord — separation always 90°."""

            def __init__(self, **kwargs):
                pass

            def transform_to(self, frame):
                class _Result:
                    def separation(self, other):
                        return _Result2()

                class _Result2:
                    deg = 90.0

                return _Result()

        monkeypatch.setattr("nova.SkyCoord", _FakeSkyCoord)

        monkeypatch.setattr("nova.get_body", lambda *a, **k: _FakeSkyCoord())

    @pytest.fixture(autouse=True)
    def _patch_get_ra_dec(self, monkeypatch):
        """Make get_ra_dec return data from the objects_map (no SIMBAD call)."""
        from nova.helpers import get_ra_dec as real_get_ra_dec

        def mock_get_ra_dec(object_name, objects_map=None):
            if objects_map:
                entry = objects_map.get(object_name.lower())
                if entry:
                    return {
                        "Object": entry.get("Object", object_name),
                        "Common Name": entry.get("Common Name", ""),
                        "RA (hours)": entry.get("RA (hours)"),
                        "DEC (degrees)": entry.get("DEC (degrees)"),
                        "Type": entry.get("Type", "N/A"),
                        "Constellation": entry.get("Constellation", "N/A"),
                        "Magnitude": entry.get("Magnitude", "N/A"),
                        "Size": entry.get("Size", "N/A"),
                        "SB": entry.get("SB", "N/A"),
                        "Project": entry.get("Project", "none"),
                    }
            return {"Object": object_name, "RA (hours)": None, "DEC (degrees)": None}

        monkeypatch.setattr("nova.helpers.get_ra_dec", mock_get_ra_dec)

    def test_cache_file_written_with_valid_json(
        self, outlook_test_db, tmp_path, monkeypatch
    ):
        """Calling update_outlook_cache() writes a JSON cache file with metadata + opportunities."""
        from nova import update_outlook_cache, cache_worker_status

        user_id = outlook_test_db["user_id"]
        location_name = outlook_test_db["location_name"]

        cache_file = str(tmp_path / "outlook_cache_test.json")
        status_key = f"test_{user_id}_{location_name}"

        user_config = {
            "locations": {
                location_name: {
                    "lat": 47.83,
                    "lon": 16.17,
                    "timezone": "Europe/Vienna",
                }
            },
            "imaging_criteria": {
                "min_observable_minutes": 60,
                "min_max_altitude": 30,
                "max_moon_illumination": 20,
                "min_angular_separation": 30,
                "search_horizon_months": 1,
            },
            "altitude_threshold": 20,
        }

        update_outlook_cache(
            user_id=user_id,
            status_key=status_key,
            cache_filename=cache_file,
            location_name=location_name,
            user_config=user_config,
            sampling_interval=15,
        )

        # 1. File exists on disk
        assert os.path.isfile(cache_file), (
            f"Cache file was not written to {cache_file}"
        )

        # 2. Valid JSON with expected top-level keys
        with open(cache_file) as f:
            data = json.load(f)

        assert "metadata" in data, "Cache file missing 'metadata' key"
        assert "opportunities" in data, "Cache file missing 'opportunities' key"

        # 3. Metadata contains expected fields
        meta = data["metadata"]
        assert meta.get("user_id") == user_id
        assert meta.get("location") == location_name
        assert "last_successful_run_utc" in meta

        # 4. Status was set to "complete" (not "running" or "error")
        assert cache_worker_status.get(status_key) == "complete", (
            f"Cache status is '{cache_worker_status.get(status_key)}', expected 'complete'"
        )

    def test_empty_cache_when_no_active_projects(
        self, outlook_test_db, tmp_path, monkeypatch
    ):
        """When a user has no active projects, the cache is written with an empty opportunities list."""
        from nova import update_outlook_cache, cache_worker_status

        user_id = outlook_test_db["user_id"]
        location_name = outlook_test_db["location_name"]

        # Clear active projects by setting active_project=False
        from nova import get_db, AstroObject

        session = get_db()
        m31 = (
            session.query(AstroObject)
            .filter_by(user_id=user_id, active_project=True)
            .first()
        )
        if m31:
            m31.active_project = False
            session.commit()

        cache_file = str(tmp_path / "outlook_cache_empty.json")
        status_key = f"test_empty_{user_id}"

        user_config = {
            "locations": {
                location_name: {
                    "lat": 47.83,
                    "lon": 16.17,
                    "timezone": "Europe/Vienna",
                },
            },
            "altitude_threshold": 20,
        }

        update_outlook_cache(
            user_id=user_id,
            status_key=status_key,
            cache_filename=cache_file,
            location_name=location_name,
            user_config=user_config,
            sampling_interval=15,
        )

        with open(cache_file) as f:
            data = json.load(f)

        assert data["opportunities"] == []
        assert cache_worker_status.get(status_key) == "complete"

    def test_cache_contains_opportunities_when_projects_active(
        self, outlook_test_db, tmp_path, monkeypatch
    ):
        """When active projects exist and pass criteria, opportunities are populated."""
        from nova import update_outlook_cache, cache_worker_status

        user_id = outlook_test_db["user_id"]
        location_name = outlook_test_db["location_name"]

        cache_file = str(tmp_path / "outlook_cache_with_data.json")
        status_key = f"test_active_{user_id}"

        user_config = {
            "locations": {
                location_name: {
                    "lat": 47.83,
                    "lon": 16.17,
                    "timezone": "Europe/Vienna",
                },
            },
            "imaging_criteria": {
                "min_observable_minutes": 60,
                "min_max_altitude": 30,
                "max_moon_illumination": 20,
                "min_angular_separation": 30,
                "search_horizon_months": 1,
            },
            "altitude_threshold": 20,
        }

        update_outlook_cache(
            user_id=user_id,
            status_key=status_key,
            cache_filename=cache_file,
            location_name=location_name,
            user_config=user_config,
            sampling_interval=15,
        )

        with open(cache_file) as f:
            data = json.load(f)

        # Our mock returns 180min observable and 65° max altitude, which passes
        # the default criteria (60min min, 30° min). Moon phase is 5% which
        # passes 20%. Separation is mocked to 90° which passes 30°.
        assert len(data["opportunities"]) > 0, (
            "Expected opportunities in cache but got none. Check mock values vs criteria."
        )

        # Each opportunity should have expected keys
        opp = data["opportunities"][0]
        assert "object_name" in opp
        assert "date" in opp
        assert "score" in opp
        assert "rating" in opp
