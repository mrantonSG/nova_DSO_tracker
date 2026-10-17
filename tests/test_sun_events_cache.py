"""/sun_events must not change the sun events it reads from SUN_EVENTS_CACHE.

calculate_sun_events_cached() hands every caller the same dict, and
/api/get_plot_data returns that dict as sun_events.current, so date, time, phase
or error written into it by /sun_events would show up there (and error would
grow on every call).
"""

import modules.astro_calculations as astro_calculations

LOC_NAME = "Default Test Loc"  # created by the conftest client fixture (lat 50, lon 10, UTC)
DATE = {"day": 15, "month": 1, "year": 2026}
EVENT_KEYS = {"astronomical_dawn", "sunrise", "transit", "sunset", "astronomical_dusk"}


def test_sun_events_leaves_the_cached_dict_unchanged(client, monkeypatch):
    monkeypatch.setattr(astro_calculations, "SUN_EVENTS_CACHE", {})
    sim_date = "{year}-{month:02d}-{day:02d}".format(**DATE)

    for _ in range(2):
        response = client.get('/sun_events', query_string={"location": LOC_NAME, "sim_date": sim_date})
        assert response.status_code == 200
        assert {"date", "time", "phase"} <= set(response.get_json())

    cached = astro_calculations.SUN_EVENTS_CACHE
    assert cached, "/sun_events did not go through calculate_sun_events_cached"
    for key, events in cached.items():
        assert set(events) == EVENT_KEYS, f"{key}: {sorted(set(events) - EVENT_KEYS)} added"

    # The same location without plot_lat/plot_lon/plot_tz: the same cache entry.
    response = client.get('/api/get_plot_data/M42', query_string={"plot_loc_name": LOC_NAME, **DATE})
    assert response.status_code == 200
    assert set(response.get_json()["sun_events"]["current"]) == EVENT_KEYS
