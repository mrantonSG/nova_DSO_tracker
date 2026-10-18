"""Heatmap rows with the same RA keep id order.

The route (get_yearly_heatmap_chunk) and the worker (heatmap_background_worker)
sort the visible objects by RA with a stable sort, so ties keep the query's order.
Both queries order by AstroObject.id, the order /api/desktop_inputs serves, so the
browser-built heatmap can reproduce the rows. The two tied objects are named so
that alphabetical order is the reverse of id order.

On SQLite the unordered query already comes back in id order (it searches the
user_id index), so this pins the order rather than reproducing a failure.
"""

import contextlib
import json
import time
import types
from datetime import timedelta

import pytest

import nova.blueprints.api as api
import nova.workers.heatmap as heatmap_worker
from nova import AstroObject, DbUser
from nova.config import astro_context_cache

LOCATION = "Default Test Loc"  # created by su_client_logged_in (lat 50, lon 10, UTC)
CHUNK_URL = f"/api/get_yearly_heatmap_chunk?location_name={LOCATION}&chunk_index=0"
TIED = ("Zeta Tie", "Alpha Tie")  # added in this order: ids ascending, names descending


class _StopWorker(BaseException):
    """BaseException so the worker's own `except Exception` can't swallow it."""


def _fake_duration(*a, **k):
    return timedelta(hours=2), 50.0, None, None


@pytest.fixture
def tied_user(su_client_logged_in, db_session, monkeypatch):
    user = db_session.query(DbUser).filter_by(username="default").one()
    # Same RA, near the zenith at lat 50: both pass the geometric filter
    for name in TIED:
        db_session.add(AstroObject(user_id=user.id, object_name=name, ra_hours=6.0,
                                   dec_deg=50.0, enabled=True))
        db_session.flush()
    db_session.commit()
    astro_context_cache.clear()

    ids = [db_session.query(AstroObject).filter_by(user_id=user.id, object_name=n).one().id
           for n in TIED]
    assert ids == sorted(ids)
    assert sorted(TIED) == list(reversed(TIED))

    monkeypatch.setattr(api, "calculate_observable_duration_vectorized", _fake_duration)
    return types.SimpleNamespace(user=user, client=su_client_logged_in, session=db_session)


def _tied_rows(ids):
    # The chunk's "ids" field holds object names (meta_ids.append(obj.object_name))
    return [name for name in ids if name in TIED]


def test_route_keeps_id_order_for_equal_ra(tied_user):
    resp = tied_user.client.get(CHUNK_URL)
    assert resp.status_code == 200
    assert _tied_rows(resp.get_json()["ids"]) == list(TIED)


def test_worker_keeps_id_order_for_equal_ra(tied_user, monkeypatch, isolated_cache_dir):
    def fake_sleep(secs):
        if secs in (60, 4 * 60 * 60):
            raise _StopWorker()

    monkeypatch.setattr(heatmap_worker, "get_db", lambda: tied_user.session)
    monkeypatch.setattr(heatmap_worker, "calculate_observable_duration_vectorized", _fake_duration)
    monkeypatch.setattr(heatmap_worker, "time", types.SimpleNamespace(sleep=fake_sleep, time=time.time))
    with pytest.raises(_StopWorker):
        heatmap_worker.heatmap_background_worker(types.SimpleNamespace(app_context=contextlib.nullcontext))

    chunks = list(isolated_cache_dir.glob(f"heatmap_v7_{tied_user.user.id}_*.part0.json"))
    assert len(chunks) == 1
    assert _tied_rows(json.loads(chunks[0].read_text())["ids"]) == list(TIED)
