"""AI routes use resolve_altitude_threshold: location override, else global, else 20.

The AI blueprint is only registered with an AI key, so the views are called
directly (same pattern as test_ai_prefilter_debug_night.py). The AI provider
and the prompt builder are stubbed: no network request is made.
  (a) notes: location null, global 30 -> 30 in the calculation and the prompt's
      location list, and max_altitude_deg is a number
  (b) prefilter_debug: location 0 -> 0
  (c) prefilter_debug: location null, global 30 -> 30
"""

import pytest
from flask import g

import modules.astro_calculations as astro
import nova.ai.routes as ai_routes
from nova import app, get_or_create_db_user, Location, AstroObject


@pytest.fixture
def recorded_thresholds(monkeypatch):
    """Spy on the duration calculation (both routes import it from the module at call time)."""
    seen = []
    real_duration = astro.calculate_observable_duration_vectorized

    def _spy(ra, dec, lat, lon, local_date, tz_name, altitude_threshold, *args, **kwargs):
        seen.append(altitude_threshold)
        return real_duration(ra, dec, lat, lon, local_date, tz_name, altitude_threshold, *args, **kwargs)

    monkeypatch.setattr(astro, "calculate_observable_duration_vectorized", _spy)
    return seen


def _user_with_location(db_session, loc_threshold):
    user = get_or_create_db_user(db_session, "default")
    db_session.add(Location(user_id=user.id, name="Home", lat=50.0, lon=10.0, timezone="UTC",
                            is_default=True, active=True, altitude_threshold=loc_threshold))
    db_session.add(AstroObject(user_id=user.id, object_name="M42", ra_hours=5.58, dec_deg=-5.4))
    db_session.commit()
    return user


def test_notes_null_location_uses_global(db_session, monkeypatch, recorded_thresholds):
    user = _user_with_location(db_session, None)

    prompt_kwargs = {}

    def _fake_prompt(object_data, **kwargs):
        prompt_kwargs.update(kwargs)
        return {"user": "u", "system": "s"}

    monkeypatch.setattr(ai_routes, "user_has_ai_access", lambda username: True)
    monkeypatch.setattr(ai_routes, "build_dso_notes_prompt", _fake_prompt)
    monkeypatch.setattr(ai_routes, "get_ai_response", lambda *a, **k: "notes")

    body = {"object_name": "M42", "selected_day": 10, "selected_month": 1, "selected_year": 2027}
    with app.test_request_context("/api/ai/notes", method="POST", json=body):
        g.db_user = user
        g.user_config = {"altitude_threshold": 30}
        resp = ai_routes.generate_dso_notes()

    assert resp.status_code == 200
    assert recorded_thresholds == [30]
    prompt_loc = prompt_kwargs["locations"][0]
    assert prompt_loc["altitude_threshold"] == 30
    assert isinstance(prompt_loc["max_altitude_deg"], (int, float))


@pytest.mark.parametrize("loc_threshold, global_threshold, expected", [
    (0, 30, 0),        # (b) 0 is a valid override, not "missing"
    (None, 30, 30),    # (c) null override falls back to the global
])
def test_prefilter_debug_threshold(db_session, recorded_thresholds,
                                   loc_threshold, global_threshold, expected):
    user = _user_with_location(db_session, loc_threshold)

    with app.test_request_context("/api/ai/prefilter_debug"):
        g.db_user = user
        g.user_config = {"altitude_threshold": global_threshold}
        ai_routes.prefilter_debug()

    assert recorded_thresholds == [expected]
