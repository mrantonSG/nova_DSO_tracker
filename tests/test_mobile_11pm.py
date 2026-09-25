from datetime import datetime as _real_datetime

import pytz

from modules.astro_calculations import ra_dec_to_alt_az
from nova.config import nightly_curves_cache


def test_mobile_11pm_uses_observing_night(client, monkeypatch):
    """At 01:00 local the observing night is the previous day; mobile alt_11pm must be that night's 23:00."""
    tz = pytz.timezone("UTC")  # timezone of "Default Test Loc"
    frozen_local = tz.localize(_real_datetime(2026, 1, 15, 1, 0, 0))

    class _FrozenDatetime(_real_datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen_local.astimezone(tz) if tz is not None else frozen_local.replace(tzinfo=None)

    # Patch both modules so the old get_utc_time_for_local_11pm would see the same frozen clock
    monkeypatch.setattr("nova.helpers.datetime", _FrozenDatetime)
    monkeypatch.setattr("modules.astro_calculations.datetime", _FrozenDatetime)
    nightly_curves_cache.clear()  # force the cache-miss path

    response = client.get('/api/mobile_data_chunk?offset=0&limit=10')
    assert response.status_code == 200
    assert any(row["Object"] == "M42" for row in response.get_json()["data"])

    m42_keys = [k for k in nightly_curves_cache if "_m42_" in k]
    assert len(m42_keys) == 1
    entry = nightly_curves_cache[m42_keys[0]]

    ra, dec, lat, lon = 5.58, -5.4, 50, 10
    # Observing night = 2026-01-14 -> 23:00 local that day
    expected_alt, _ = ra_dec_to_alt_az(ra, dec, lat, lon, "2026-01-14T23:00:00")
    # What the old clock-based code picks: 23:00 on the frozen calendar day
    old_alt, _ = ra_dec_to_alt_az(ra, dec, lat, lon, "2026-01-15T23:00:00")
    assert f"{expected_alt:.2f}" != f"{old_alt:.2f}", "guard: test would not discriminate"

    assert entry["alt_11pm"] == f"{expected_alt:.2f}"
