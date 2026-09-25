from datetime import datetime as _real_datetime, time as _time, timedelta

import ephem
import pytz
from flask import g

import modules.astro_calculations as astro
import nova.ai.routes as ai_routes
from nova import app, get_or_create_db_user, Location, AstroObject


def test_prefilter_debug_uses_location_observing_night(db_session, monkeypatch):
    """14:00 in Singapore is 06:00 UTC: the local observing night is today, the UTC noon rule says yesterday."""
    sgt = pytz.timezone("Asia/Singapore")
    frozen_local = sgt.localize(_real_datetime(2026, 3, 15, 14, 0, 0))

    class _FrozenDatetime(_real_datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen_local.astimezone(tz) if tz is not None else frozen_local.replace(tzinfo=None)

    monkeypatch.setattr(ai_routes, "datetime", _FrozenDatetime)

    user = get_or_create_db_user(db_session, "default")
    db_session.add(Location(user_id=user.id, name="Singapore", lat=1.35, lon=103.82,
                            timezone="Asia/Singapore", is_default=True))
    db_session.add(AstroObject(user_id=user.id, object_name="M42", ra_hours=5.58, dec_deg=-5.4))
    db_session.commit()

    seen_dates = []
    real_duration = astro.calculate_observable_duration_vectorized

    def _spy(ra, dec, lat, lon, local_date, *args, **kwargs):
        seen_dates.append(local_date)
        return real_duration(ra, dec, lat, lon, local_date, *args, **kwargs)

    monkeypatch.setattr(astro, "calculate_observable_duration_vectorized", _spy)

    with app.test_request_context("/api/ai/prefilter_debug"):
        g.db_user = user
        g.user_config = {}
        body = ai_routes.prefilter_debug().get_json()

    # Observing night in Singapore = 2026-03-15 -> 23:00 SGT = 15:00 UTC
    expected_night = "2026-03-15"
    expected_phase = round(ephem.Moon(sgt.localize(_real_datetime(2026, 3, 15, 23, 0)).astimezone(pytz.utc)).phase, 1)

    # Guard: the old UTC-based derivation picks a different night and moon instant
    now_utc = frozen_local.astimezone(pytz.utc)
    old_date = now_utc.date() if now_utc.hour >= 12 else now_utc.date() - timedelta(days=1)
    old_night = old_date.isoformat()
    old_phase = round(ephem.Moon(pytz.utc.localize(_real_datetime.combine(old_date, _time(23, 0)))).phase, 1)
    assert old_night != expected_night, "guard: night would not discriminate"
    assert old_phase != expected_phase, "guard: moon phase would not discriminate"

    assert seen_dates == [expected_night]
    assert body["moon_phase"] == expected_phase
