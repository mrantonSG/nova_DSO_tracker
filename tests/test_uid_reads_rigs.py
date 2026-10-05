"""
record_uid reads for a session's and a framing's rig (step 5 part 3).

The rig is found by user + record_uid; the row number in rig_id_snapshot /
rig_id never decides, and a deleted rig never loses its stored specs.

The read surfaces are the AI routes (session summary, DSO notes), the mobile
mosaic and /api/get_framing, plus the two lookup helpers.
"""
from datetime import date

import pytest
from flask import g

import nova.ai.routes as ai_routes
from nova import app
from nova.models import AstroObject, Component, DbUser, JournalSession, Rig, SavedFraming
from nova.record_links import rig_for_uid, rigs_by_uid, sync_rig_links


# --- helpers --------------------------------------------------------------------

def _components(db, user_id, tag, guide=False, focal_mm=480, guide_pixel_um=2.9):
    tel = Component(user_id=user_id, kind="telescope", name=f"{tag} Scope",
                    aperture_mm=80, focal_length_mm=focal_mm)
    cam = Component(user_id=user_id, kind="camera", name=f"{tag} Cam",
                    sensor_width_mm=23.5, sensor_height_mm=15.6, pixel_size_um=3.76)
    db.add_all([tel, cam])
    db.flush()
    comps = {"tel": tel, "cam": cam}
    if guide:
        gt = Component(user_id=user_id, kind="telescope", name=f"{tag} Guide Scope",
                       focal_length_mm=200)
        gc = Component(user_id=user_id, kind="camera", name=f"{tag} Guide Cam",
                       pixel_size_um=guide_pixel_um)
        db.add_all([gt, gc])
        db.flush()
        comps.update({"guide_tel": gt, "guide_cam": gc})
    db.flush()
    return comps


def _rig(db, user_id, tag, comps, guide=False):
    rig = Rig(user_id=user_id, rig_name=f"{tag} Rig",
              telescope_id=comps["tel"].id, camera_id=comps["cam"].id,
              effective_focal_length=384.0, f_ratio=4.8, image_scale=2.0, fov_w_arcmin=100.0)
    if guide:
        rig.guide_telescope_id = comps["guide_tel"].id
        rig.guide_camera_id = comps["guide_cam"].id
    sync_rig_links(db, rig)
    db.add(rig)
    db.flush()
    return rig


def _session(db, user_id, obj="M42", rig=None, **cols):
    s = JournalSession(user_id=user_id, object_name=obj, date_utc=date(2026, 1, 10))
    for key, value in cols.items():
        setattr(s, key, value)
    if rig is not None:
        s.rig_id_snapshot = rig.id
        s.rig_record_uid = rig.record_uid
    db.add(s)
    db.flush()
    return s


def _framing(db, user_id, obj="M42", rig=None, **cols):
    f = SavedFraming(user_id=user_id, object_name=obj, ra=83.8, dec=-5.4, rotation=0.0,
                     mosaic_cols=1, mosaic_rows=1, mosaic_overlap=10.0)
    for key, value in cols.items():
        setattr(f, key, value)
    if rig is not None:
        f.rig_id = rig.id
        f.rig_record_uid = rig.record_uid
        f.rig_name = rig.rig_name
    db.add(f)
    db.flush()
    return f


def _edit(client, session_id, **data):
    payload = {"session_date": "2026-01-10", "target_object_id": "", "location_name": ""}
    payload.update(data)
    resp = client.post(f"/journal/edit/{session_id}", data=payload)
    assert resp.status_code == 302
    return resp


def _session_summary(db, session_id, user, monkeypatch):
    """Call the AI session-summary route with the prompt builder stubbed."""
    seen = {}

    def _fake_prompt(session_data, **kwargs):
        seen.update(session_data)
        return {"user": "u", "system": "s"}

    monkeypatch.setattr(ai_routes, "build_session_summary_prompt", _fake_prompt)
    monkeypatch.setattr(ai_routes, "get_ai_response", lambda *a, **k: iter(()))
    with app.test_request_context("/api/ai/session-summary", method="POST",
                                  json={"session_id": session_id}):
        g.db_user = user
        resp = ai_routes.generate_session_summary()
        assert resp.status_code == 200
    return seen


def _dso_notes(db, user, monkeypatch):
    """Call the AI notes route with the prompt builder stubbed; returns its kwargs."""
    ctx = {}

    def _fake_prompt(object_data, **kwargs):
        ctx.update(kwargs)
        return {"user": "u", "system": "s"}

    monkeypatch.setattr(ai_routes, "build_dso_notes_prompt", _fake_prompt)
    monkeypatch.setattr(ai_routes, "get_ai_response", lambda *a, **k: "notes")
    body = {"object_name": "M42", "selected_day": 10, "selected_month": 1, "selected_year": 2027}
    with app.test_request_context("/api/ai/notes", method="POST", json=body):
        g.db_user = user
        g.user_config = {"altitude_threshold": 20}
        resp = ai_routes.generate_dso_notes()
        assert resp.status_code == 200
    return ctx


@pytest.fixture
def mu(multi_user_client):
    client, ids = multi_user_client
    return client, ids["user_a_id"], ids["user_b_id"]


@pytest.fixture(autouse=True)
def _ai_stubs(monkeypatch):
    monkeypatch.setattr(ai_routes, "user_has_ai_access", lambda username: True)


# --- helpers --------------------------------------------------------------------

def test_rig_helpers_follow_uid_and_never_another_user(mu, db_session):
    _, a_id, b_id = mu
    comps_a = _components(db_session, a_id, "A")
    comps_b = _components(db_session, b_id, "B")
    rig_a = _rig(db_session, a_id, "A", comps_a)
    rig_b = _rig(db_session, b_id, "B", comps_b)
    db_session.commit()

    assert rig_for_uid(db_session, a_id, rig_a.record_uid) is rig_a
    assert rig_for_uid(db_session, a_id, rig_b.record_uid) is None
    assert rig_for_uid(db_session, a_id, None) is None
    assert rig_for_uid(db_session, a_id, "no-such-uid") is None

    by_uid = rigs_by_uid(db_session, a_id)
    assert set(by_uid) == {rig_a.record_uid}
    assert {r.user_id for r in by_uid.values()} == {a_id}


# --- the row number is reused by a newer rig ------------------------------------

def test_session_reused_row_number_never_uses_the_newer_rig(mu, db_session, monkeypatch):
    _, a_id, _ = mu
    comps_old = _components(db_session, a_id, "Old", guide=True, guide_pixel_um=2.9)
    old = _rig(db_session, a_id, "Old", comps_old, guide=True)
    comps_new = _components(db_session, a_id, "New", guide=True, guide_pixel_um=5.0)
    newer = _rig(db_session, a_id, "New", comps_new, guide=True)
    s = _session(db_session, a_id, rig=old)
    s.rig_id_snapshot = newer.id          # the row number now names the newer rig
    db_session.commit()

    user_a = db_session.get(DbUser, a_id)
    seen = _session_summary(db_session, s.id, user_a, monkeypatch)

    assert seen["guide_pixel_um"] == pytest.approx(2.9)   # the UID's rig, not the newer one


def test_framing_reused_row_number_never_uses_the_newer_rig(mu, db_session, monkeypatch):
    client, a_id, _ = mu
    m42 = AstroObject(user_id=a_id, object_name="M42", ra_hours=5.58, dec_deg=-5.4)
    db_session.add(m42)
    comps_old = _components(db_session, a_id, "Old")
    old = _rig(db_session, a_id, "Old", comps_old)
    comps_new = _components(db_session, a_id, "New", focal_mm=1000)
    newer = _rig(db_session, a_id, "New", comps_new)
    f = _framing(db_session, a_id, rig=old)
    f.object_record_uid = m42.record_uid   # the framing belongs to the object by UID
    f.rig_id = newer.id                   # the row number now names the newer rig
    db_session.commit()

    # The read API reports the UID and the stored row number as they are.
    data = client.get("/api/get_framing/M42").get_json()
    assert data["rig_uid"] == old.record_uid
    assert data["rig"] == newer.id

    # The AI framing context comes from the UID's rig.
    ctx = _dso_notes(db_session, db_session.get(DbUser, a_id), monkeypatch)
    assert ctx["framing_context"]["rig_name"] == "Old Rig"


# --- a deleted rig never loses its link or its stored specs ---------------------

def test_deleted_rig_keeps_specs_and_link_on_resave(mu, db_session):
    client, a_id, _ = mu
    comps = _components(db_session, a_id, "A")
    rig = _rig(db_session, a_id, "A", comps)
    specs = {"rig_name_snapshot": "A Rig", "rig_efl_snapshot": 480.0, "rig_fr_snapshot": 6.0,
             "rig_scale_snapshot": 1.6, "rig_fov_w_snapshot": 100.0, "rig_fov_h_snapshot": 70.0,
             "telescope_name_snapshot": "A Scope", "reducer_name_snapshot": None,
             "camera_name_snapshot": "A Cam"}
    s = _session(db_session, a_id, rig=rig, **specs)
    db_session.commit()
    sid, uid, rig_id = s.id, s.rig_record_uid, s.rig_id_snapshot

    db_session.delete(rig)                # the FK is not enforced: the link row stays
    db_session.commit()

    _edit(client, sid, rig_id_snapshot="")
    db_session.expire_all()
    s = db_session.get(JournalSession, sid)
    assert s.rig_id_snapshot == rig_id
    assert s.rig_record_uid == uid
    for key, value in specs.items():
        assert getattr(s, key) == value, key


def test_session_with_specs_but_no_uid_keeps_specs_on_resave(mu, db_session):
    """After a journal import the specs can survive with no UID at all (rule 4)."""
    client, a_id, _ = mu
    s = _session(db_session, a_id, rig_name_snapshot="Imported Rig",
                 rig_efl_snapshot=480.0, telescope_name_snapshot="Imported Scope")
    db_session.commit()
    sid = s.id

    _edit(client, sid, rig_id_snapshot="")
    db_session.expire_all()
    s = db_session.get(JournalSession, sid)
    assert s.rig_id_snapshot is None
    assert s.rig_record_uid is None
    assert s.rig_name_snapshot == "Imported Rig"
    assert s.rig_efl_snapshot == 480.0
    assert s.telescope_name_snapshot == "Imported Scope"


# --- rule 9: the same rig does not rewrite the stored specs ---------------------

def test_same_rig_resave_after_components_change_keeps_specs(mu, db_session):
    client, a_id, _ = mu
    comps = _components(db_session, a_id, "A")
    rig = _rig(db_session, a_id, "A", comps)
    s = _session(db_session, a_id, rig=rig,
                 rig_efl_snapshot=480.0, rig_name_snapshot="A Rig")
    db_session.commit()
    sid = s.id
    comps["tel"].focal_length_mm = 1000
    db_session.commit()

    _edit(client, sid, rig_id_snapshot=str(rig.id))
    db_session.expire_all()
    s = db_session.get(JournalSession, sid)
    assert s.rig_efl_snapshot == 480.0          # not recomputed
    assert s.rig_record_uid == rig.record_uid


def test_different_rig_resubmitted_recomputes_specs_and_changes_uid(mu, db_session):
    client, a_id, _ = mu
    comps1 = _components(db_session, a_id, "One")
    rig1 = _rig(db_session, a_id, "One", comps1)
    comps2 = _components(db_session, a_id, "Two", focal_mm=700)
    rig2 = _rig(db_session, a_id, "Two", comps2)
    s = _session(db_session, a_id, rig=rig1, rig_efl_snapshot=480.0, rig_name_snapshot="One Rig")
    db_session.commit()
    sid = s.id

    _edit(client, sid, rig_id_snapshot=str(rig2.id))
    db_session.expire_all()
    s = db_session.get(JournalSession, sid)
    assert s.rig_id_snapshot == rig2.id
    assert s.rig_record_uid == rig2.record_uid
    assert s.rig_name_snapshot == "Two Rig"
    assert s.rig_efl_snapshot == pytest.approx(700.0)   # recomputed from the new rig


# --- Q1 exception: heal a session whose specs are all blank ---------------------

def test_same_rig_resave_fills_only_a_fully_blank_session(mu, db_session):
    client, a_id, _ = mu
    comps = _components(db_session, a_id, "A")
    rig = _rig(db_session, a_id, "A", comps)
    s = _session(db_session, a_id, rig=rig, rig_name_snapshot="Kept Name")
    db_session.commit()
    sid, uid = s.id, s.rig_record_uid

    _edit(client, sid, rig_id_snapshot=str(rig.id))
    db_session.expire_all()
    s = db_session.get(JournalSession, sid)
    assert s.rig_record_uid == uid
    assert s.rig_efl_snapshot == pytest.approx(480.0)
    assert s.telescope_name_snapshot == "A Scope"
    assert s.camera_name_snapshot == "A Cam"
    assert s.rig_name_snapshot == "Kept Name"   # a present name is not overwritten


def test_same_rig_resave_with_any_spec_keeps_every_spec(mu, db_session):
    client, a_id, _ = mu
    comps = _components(db_session, a_id, "A")
    rig = _rig(db_session, a_id, "A", comps)
    s = _session(db_session, a_id, rig=rig, rig_efl_snapshot=999.0)   # one spec present
    db_session.commit()
    sid = s.id

    _edit(client, sid, rig_id_snapshot=str(rig.id))
    db_session.expire_all()
    s = db_session.get(JournalSession, sid)
    assert s.rig_efl_snapshot == 999.0
    assert s.rig_fr_snapshot is None            # not filled: the specs were not fully blank
    assert s.telescope_name_snapshot is None


# --- no rig is ever picked by name ----------------------------------------------

def test_ai_never_picks_a_rig_by_name(mu, db_session, monkeypatch):
    _, a_id, _ = mu
    db_session.add(AstroObject(user_id=a_id, object_name="M42", ra_hours=5.58, dec_deg=-5.4))
    comps = _components(db_session, a_id, "A", guide=True)
    _rig(db_session, a_id, "Named", comps, guide=True)                 # same name, has guide optics
    s = _session(db_session, a_id, rig_name_snapshot="Named Rig")      # no id, no UID
    _framing(db_session, a_id, rig_name="Named Rig")                   # no id, no UID
    db_session.commit()

    seen = _session_summary(db_session, s.id, db_session.get(DbUser, a_id), monkeypatch)
    assert seen["guide_pixel_um"] is None

    ctx = _dso_notes(db_session, db_session.get(DbUser, a_id), monkeypatch)
    assert ctx["framing_context"] is None


# --- another user's rig is never resolved ---------------------------------------

def test_foreign_uid_is_never_resolved_and_other_user_unchanged(mu, db_session, monkeypatch):
    client, a_id, b_id = mu
    comps_b = _components(db_session, b_id, "B", guide=True)
    rig_b = _rig(db_session, b_id, "B", comps_b, guide=True)
    comps_a = _components(db_session, a_id, "A")
    rig_a = _rig(db_session, a_id, "A", comps_a)
    s = _session(db_session, a_id, rig=rig_a)
    s.rig_id_snapshot = rig_b.id               # even the row number names B's rig
    s.rig_record_uid = rig_b.record_uid
    f = _framing(db_session, a_id, rig=None)
    f.rig_id = rig_b.id
    f.rig_record_uid = rig_b.record_uid
    f.rig_name = "B Rig"
    db_session.commit()

    seen = _session_summary(db_session, s.id, db_session.get(DbUser, a_id), monkeypatch)
    assert seen["guide_pixel_um"] is None

    resp = client.get("/m/mosaic/M42")
    assert b"Rig data missing in saved framing." in resp.data

    db_session.expire_all()
    unchanged = db_session.get(Rig, rig_b.id)
    assert (unchanged.user_id, unchanged.rig_name) == (b_id, "B Rig")
    assert {r.user_id for r in db_session.query(Rig).all() if r.user_id == a_id} == {a_id}
