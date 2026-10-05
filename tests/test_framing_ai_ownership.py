"""
Ownership checks for saved framings and the AI routes.
User A (logged in) must not be able to store, or read back, user B's rig
through a saved framing, and the AI routes must only use A's own objects
and rigs. Multi-user mode only.

The AI blueprint is only registered with an AI key, so the AI views are
called directly (same pattern as test_ai_altitude_threshold.py) with the
provider and prompt builders stubbed: no network request is made.
"""
from datetime import date

import pytest
from flask import g

import nova.ai.routes as ai_routes
from nova import app
from nova.models import AstroObject, Component, DbUser, JournalSession, Rig, SavedFraming


def _make_rig(db_session, user_id, name, guide=False):
    scope = Component(user_id=user_id, kind="telescope", name=f"{name} Scope",
                      aperture_mm=200, focal_length_mm=1000)
    cam = Component(user_id=user_id, kind="camera", name=f"{name} Cam",
                    sensor_width_mm=36, sensor_height_mm=24, pixel_size_um=5.9)
    db_session.add_all([scope, cam])
    db_session.flush()
    rig = Rig(user_id=user_id, rig_name=name, telescope_id=scope.id, camera_id=cam.id,
              effective_focal_length=1000, f_ratio=5.0, image_scale=1.22, fov_w_arcmin=123.8)
    if guide:
        g_scope = Component(user_id=user_id, kind="telescope", name=f"{name} Guide Scope",
                            aperture_mm=50, focal_length_mm=777)
        g_cam = Component(user_id=user_id, kind="camera", name=f"{name} Guide Cam",
                          sensor_width_mm=5, sensor_height_mm=4, pixel_size_um=9.99)
        db_session.add_all([g_scope, g_cam])
        db_session.flush()
        rig.guide_telescope_id = g_scope.id
        rig.guide_camera_id = g_cam.id
    db_session.add(rig)
    db_session.commit()
    return rig


def test_save_framing_rejects_foreign_rig(multi_user_client, db_session):
    client, ids = multi_user_client
    rig_b = _make_rig(db_session, ids["user_b_id"], "B Secret Rig")
    rig_b_id = rig_b.id

    resp = client.post('/api/save_framing', json={
        'object_name': "M42", 'rig': rig_b_id, 'ra': 83.8, 'dec': -5.4,
    })

    assert resp.status_code == 200
    assert resp.get_json()["status"] == "success"
    db_session.expire_all()
    framing = db_session.query(SavedFraming).filter_by(
        user_id=ids["user_a_id"], object_name="M42").one()
    assert framing.rig_id is None
    assert framing.rig_name is None
    rig = db_session.get(Rig, rig_b_id)
    assert rig.user_id == ids["user_b_id"]
    assert rig.rig_name == "B Secret Rig"


def test_existing_framing_with_foreign_rig_does_not_leak(multi_user_client, db_session, monkeypatch):
    """A framing row that already holds B's rig id and name (written before the
    fix) must not expose that rig's name or specs on any read path."""
    client, ids = multi_user_client
    rig_b = _make_rig(db_session, ids["user_b_id"], "B Secret Rig")
    db_session.add(AstroObject(user_id=ids["user_a_id"], object_name="M42",
                               ra_hours=5.58, dec_deg=-5.4, enabled=True))
    db_session.add(SavedFraming(user_id=ids["user_a_id"], object_name="M42",
                                rig_id=rig_b.id, rig_name="B Secret Rig",
                                rig_record_uid=rig_b.record_uid,
                                ra=83.8, dec=-5.4, rotation=0.0,
                                mosaic_cols=2, mosaic_rows=2, mosaic_overlap=10.0))
    db_session.commit()

    # 1. Desktop batch: the rig label is not B's name.
    resp = client.get('/api/get_desktop_data_batch', query_string={"location": "UserA_Home"})
    assert resp.status_code == 200
    m42 = next(r for r in resp.get_json()["results"] if r.get("Object") == "M42")
    assert m42["framing_rig"] == ""
    assert b"B Secret Rig" not in resp.data

    # 2. Mobile mosaic: no plan is built from B's rig.
    resp = client.get('/m/mosaic/M42')
    assert resp.status_code == 200
    assert b"Error: Rig data missing in saved framing." in resp.data

    # 3. AI notes: no framing context from B's rig reaches the prompt.
    user_a = db_session.get(DbUser, ids["user_a_id"])
    prompt_kwargs = {}

    def _fake_prompt(object_data, **kwargs):
        prompt_kwargs.update(kwargs)
        return {"user": "u", "system": "s"}

    monkeypatch.setattr(ai_routes, "user_has_ai_access", lambda username: True)
    monkeypatch.setattr(ai_routes, "build_dso_notes_prompt", _fake_prompt)
    monkeypatch.setattr(ai_routes, "get_ai_response", lambda *a, **k: "notes")

    body = {"object_name": "M42", "selected_day": 10, "selected_month": 1, "selected_year": 2027}
    with app.test_request_context("/api/ai/notes", method="POST", json=body):
        g.db_user = user_a
        g.user_config = {"altitude_threshold": 20}
        resp = ai_routes.generate_dso_notes()

    assert resp.status_code == 200
    assert prompt_kwargs["framing_context"] is None


@pytest.mark.parametrize("case", ["snapshot_id", "name_fallback"])
def test_session_summary_ignores_foreign_rig(multi_user_client, db_session, monkeypatch, case):
    _, ids = multi_user_client
    rig_b = _make_rig(db_session, ids["user_b_id"], "Shared Name Rig", guide=True)
    if case == "snapshot_id":
        # Old bad row: A's session points straight at B's rig.
        rig = rig_b
    else:
        # A's own same-named rig has no guide optics; the UID must not fall
        # back to B's guide-equipped rig.
        rig = _make_rig(db_session, ids["user_a_id"], "Shared Name Rig A")
    session = JournalSession(user_id=ids["user_a_id"], object_name="M42",
                             date_utc=date(2026, 1, 10), rig_id_snapshot=rig.id,
                             rig_record_uid=rig.record_uid,
                             rig_name_snapshot="Shared Name Rig")
    db_session.add(session)
    db_session.commit()
    user_a = db_session.get(DbUser, ids["user_a_id"])

    seen = {}

    def _fake_prompt(session_data, **kwargs):
        seen.update(session_data)
        return {"user": "u", "system": "s"}

    monkeypatch.setattr(ai_routes, "user_has_ai_access", lambda username: True)
    monkeypatch.setattr(ai_routes, "build_session_summary_prompt", _fake_prompt)
    monkeypatch.setattr(ai_routes, "get_ai_response", lambda *a, **k: iter(()))

    with app.test_request_context("/api/ai/session-summary", method="POST",
                                  json={"session_id": session.id}):
        g.db_user = user_a
        resp = ai_routes.generate_session_summary()

    assert resp.status_code == 200
    assert seen["guide_pixel_um"] is None
    assert seen["guide_FL_mm"] is None
    db_session.expire_all()
    rig = db_session.get(Rig, rig_b.id)
    assert rig.user_id == ids["user_b_id"]


@pytest.mark.parametrize("lookup", ["object_id", "object_name"])
def test_ai_notes_foreign_object_404(multi_user_client, db_session, monkeypatch, lookup):
    _, ids = multi_user_client
    obj_b = AstroObject(user_id=ids["user_b_id"], object_name="B Only Object",
                        ra_hours=1.0, dec_deg=2.0)
    db_session.add(obj_b)
    db_session.commit()
    obj_b_id = obj_b.id
    user_a = db_session.get(DbUser, ids["user_a_id"])

    called = []
    monkeypatch.setattr(ai_routes, "user_has_ai_access", lambda username: True)
    monkeypatch.setattr(ai_routes, "get_ai_response", lambda *a, **k: called.append(1) or "notes")

    body = {"object_id": obj_b_id} if lookup == "object_id" else {"object_name": "B Only Object"}
    with app.test_request_context("/api/ai/notes", method="POST", json=body):
        g.db_user = user_a
        g.user_config = {"altitude_threshold": 20}
        resp = ai_routes.generate_dso_notes()

    status = resp[1] if isinstance(resp, tuple) else resp.status_code
    assert status == 404
    assert called == []
    db_session.expire_all()
    obj = db_session.get(AstroObject, obj_b_id)
    assert obj.user_id == ids["user_b_id"]
    assert obj.object_name == "B Only Object"
