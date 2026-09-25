import json

from nova import app
from nova.config import nightly_curves_cache


def test_get_object_data_cache_hit_with_skyglow(client, monkeypatch, tmp_path):
    """A cache hit with skyglow data present must not raise (UnboundLocalError on alt_11pm)."""
    # 1. First request: no skyglow data -> cache miss populates nightly_curves_cache
    monkeypatch.delenv("NASA_EARTHDATA_TOKEN", raising=False)
    first = client.get('/api/get_object_data/M42')
    assert first.status_code == 200
    assert len(nightly_curves_cache) == 1

    # 2. Supply skyglow data: token + <instance_path>/skyglow/<location_id>.json
    from nova.models import Location
    from nova import get_db
    loc_id = get_db().query(Location).filter_by(name="Default Test Loc").one().id
    sg_dir = tmp_path / "skyglow"
    sg_dir.mkdir()
    (sg_dir / f"{loc_id}.json").write_text(json.dumps({
        "skyglow_horizon": [{"az_deg": az, "min_alt_deg": 90.0} for az in range(0, 360, 10)]
    }))
    monkeypatch.setenv("NASA_EARTHDATA_TOKEN", "test-token")
    monkeypatch.setattr(app, "instance_path", str(tmp_path))

    # Guarantee the second request is served from cache
    def _no_recompute(*a, **kw):
        raise AssertionError("cache miss: nightly curve was recomputed")
    monkeypatch.setattr('nova.blueprints.api.calculate_observable_duration_vectorized', _no_recompute)

    # 3. Second request: cache hit with sg_data present
    response = client.get('/api/get_object_data/M42')
    data = response.get_json()

    assert response.status_code == 200
    assert data['error'] is False
    # Floor is 90° everywhere and there is no horizon mask, so the 11PM check must run and flag it
    assert data['below_skyglow_floor_11pm'] is True
