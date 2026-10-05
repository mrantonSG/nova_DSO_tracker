"""
Ownership checks for the rig/component routes in the tools blueprint.
User A (logged in) must not be able to change or delete user B's rigs or
components, or point a rig at B's components. Multi-user mode only.
"""
import pytest

from nova.models import Component, Rig


def _get_flashes(client):
    with client.session_transaction() as sess:
        return list(sess.get('_flashes', []))


def _make_rig(db_session, user_id, prefix):
    scope = Component(user_id=user_id, kind="telescope", name=f"{prefix} Scope",
                      aperture_mm=80, focal_length_mm=480)
    cam = Component(user_id=user_id, kind="camera", name=f"{prefix} Cam",
                    sensor_width_mm=23.5, sensor_height_mm=15.6, pixel_size_um=3.76)
    db_session.add_all([scope, cam])
    db_session.commit()
    rig = Rig(user_id=user_id, rig_name=f"{prefix} Rig",
              telescope_id=scope.id, camera_id=cam.id)
    db_session.add(rig)
    db_session.commit()
    return scope, cam, rig


@pytest.fixture
def mu(multi_user_client, monkeypatch):
    monkeypatch.setattr('nova.blueprints.tools.SINGLE_USER_MODE', False)
    return multi_user_client


def test_delete_rig_other_user_forbidden(mu, db_session):
    client, ids = mu
    _, _, rig_b = _make_rig(db_session, ids["user_b_id"], "B")
    rig_b_id = rig_b.id

    resp = client.post('/delete_rig', data={'rig_id': rig_b_id})

    assert resp.status_code == 302
    assert ('error', "Rig not found.") in _get_flashes(client)
    db_session.expire_all()
    rig = db_session.get(Rig, rig_b_id)
    assert rig is not None
    assert rig.rig_name == "B Rig"


def test_add_rig_update_other_user_forbidden(mu, db_session):
    client, ids = mu
    scope_b, cam_b, rig_b = _make_rig(db_session, ids["user_b_id"], "B")
    scope_a, cam_a, _ = _make_rig(db_session, ids["user_a_id"], "A")
    rig_b_id, scope_b_id, cam_b_id = rig_b.id, scope_b.id, cam_b.id

    resp = client.post('/add_rig', data={
        'rig_id': rig_b_id,
        'rig_name': "Hijacked",
        'telescope_id': scope_a.id,
        'camera_id': cam_a.id,
    })

    assert resp.status_code == 302
    assert ('error', "Rig not found.") in _get_flashes(client)
    db_session.expire_all()
    rig = db_session.get(Rig, rig_b_id)
    assert rig.rig_name == "B Rig"
    assert rig.telescope_id == scope_b_id
    assert rig.camera_id == cam_b_id
    assert rig.user_id == ids["user_b_id"]


def test_delete_component_other_user_forbidden(mu, db_session):
    client, ids = mu
    comp_b = Component(user_id=ids["user_b_id"], kind="telescope", name="B Unused Scope",
                       aperture_mm=100, focal_length_mm=700)
    db_session.add(comp_b)
    db_session.commit()
    comp_b_id = comp_b.id

    resp = client.post('/delete_component', data={'component_id': comp_b_id})

    assert resp.status_code == 302
    assert ('error', "Component not found.") in _get_flashes(client)
    db_session.expire_all()
    comp = db_session.get(Component, comp_b_id)
    assert comp is not None
    assert comp.name == "B Unused Scope"


@pytest.mark.parametrize("field", [
    "telescope_id", "camera_id", "reducer_extender_id",
    "guide_telescope_id", "guide_camera_id",
])
def test_add_rig_rejects_foreign_components(mu, db_session, field):
    client, ids = mu
    scope_a = Component(user_id=ids["user_a_id"], kind="telescope", name="A Scope",
                        aperture_mm=80, focal_length_mm=480)
    cam_a = Component(user_id=ids["user_a_id"], kind="camera", name="A Cam",
                      sensor_width_mm=23.5, sensor_height_mm=15.6, pixel_size_um=3.76)
    kind = {"telescope_id": "telescope", "guide_telescope_id": "telescope",
            "camera_id": "camera", "guide_camera_id": "camera",
            "reducer_extender_id": "reducer_extender"}[field]
    comp_b = Component(user_id=ids["user_b_id"], kind=kind, name="B Part",
                       aperture_mm=200, focal_length_mm=1000,
                       sensor_width_mm=36, sensor_height_mm=24, pixel_size_um=5.9,
                       factor=0.8)
    db_session.add_all([scope_a, cam_a, comp_b])
    db_session.commit()
    comp_b_id = comp_b.id

    data = {'rig_name': "A New Rig", 'telescope_id': scope_a.id, 'camera_id': cam_a.id}
    data[field] = comp_b_id
    resp = client.post('/add_rig', data=data)

    assert resp.status_code == 302
    assert ('error', "Component not found.") in _get_flashes(client)
    db_session.expire_all()
    assert db_session.query(Rig).filter_by(user_id=ids["user_a_id"]).count() == 0
    comp = db_session.get(Component, comp_b_id)
    assert comp.user_id == ids["user_b_id"]
    assert comp.name == "B Part"


def test_delete_component_not_blocked_by_other_users_rig(mu, db_session):
    """A's own component can be deleted even if B's rig points at it (bad
    legacy data); B's rig itself is left in place."""
    client, ids = mu
    comp_a = Component(user_id=ids["user_a_id"], kind="telescope", name="A Scope",
                       aperture_mm=80, focal_length_mm=480)
    db_session.add(comp_a)
    db_session.commit()
    rig_b = Rig(user_id=ids["user_b_id"], rig_name="B Rig", telescope_id=comp_a.id)
    db_session.add(rig_b)
    db_session.commit()
    comp_a_id, rig_b_id = comp_a.id, rig_b.id

    resp = client.post('/delete_component', data={'component_id': comp_a_id})

    assert resp.status_code == 302
    assert ('success', "Component deleted successfully.") in _get_flashes(client)
    db_session.expire_all()
    assert db_session.get(Component, comp_a_id) is None
    rig = db_session.get(Rig, rig_b_id)
    assert rig is not None
    assert rig.rig_name == "B Rig"


def test_add_rig_with_own_components_still_saves(mu, db_session):
    """A rig posted the way config_form.html sends it today (empty rig_id,
    empty optional selects, own telescope and camera) is still created."""
    client, ids = mu
    scope_a = Component(user_id=ids["user_a_id"], kind="telescope", name="A Scope",
                        aperture_mm=80, focal_length_mm=480)
    cam_a = Component(user_id=ids["user_a_id"], kind="camera", name="A Cam",
                      sensor_width_mm=23.5, sensor_height_mm=15.6, pixel_size_um=3.76)
    db_session.add_all([scope_a, cam_a])
    db_session.commit()
    scope_a_id, cam_a_id = scope_a.id, cam_a.id

    resp = client.post('/add_rig', data={
        'rig_id': "",
        'rig_name': "A Form Rig",
        'telescope_id': scope_a_id,
        'camera_id': cam_a_id,
        'reducer_extender_id': "",
        'guide_telescope_id': "",
        'guide_camera_id': "",
    })

    assert resp.status_code == 302
    assert ('success', "Rig 'A Form Rig' created successfully.") in _get_flashes(client)
    db_session.expire_all()
    rig = db_session.query(Rig).filter_by(user_id=ids["user_a_id"], rig_name="A Form Rig").one()
    assert rig.telescope_id == scope_a_id
    assert rig.camera_id == cam_a_id
    assert rig.reducer_extender_id is None
    assert rig.guide_telescope_id is None
    assert rig.guide_camera_id is None
    assert rig.effective_focal_length == 480
