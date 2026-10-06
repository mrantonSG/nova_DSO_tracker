"""Diff 6a: one exporter (export_user_data), union of fields, UIDs and links,
no cross-user leakage, and old importers still accept the new files."""
import io
import json
import zipfile
from datetime import date
from types import SimpleNamespace

import yaml

from nova.models import (
    AstroObject, Component, DbUser, JournalSession, Location, Project, Rig,
    SavedFraming, UiPref,
)
from nova.migration import (
    _migrate_components_and_rigs, _migrate_journal, _migrate_locations,
    _migrate_objects, _migrate_saved_framings, export_user_data, export_user_to_yaml,
)
from nova.record_links import (
    sync_framing_links, sync_project_link, sync_rig_links, sync_session_links,
)


def _user(db, username):
    u = db.query(DbUser).filter_by(username=username).one_or_none()
    if u is None:
        u = DbUser(username=username)
        db.add(u)
        db.flush()
    return u


def _populate(db, username, *, obj="M42", rig_name="Main"):
    """A location, object, telescope, camera, rig, framing, project and session.

    Object names are stored normalized ("M42"), as the importer produces them.
    """
    u = _user(db, username)
    loc = Location(user_id=u.id, name="Home", lat=1.0, lon=2.0, timezone="UTC",
                   bortle_scale=4, elevation=120.0, sqm_zenith=21.3)
    obj_row = AstroObject(user_id=u.id, object_name=obj, ra_hours=1.0, dec_deg=2.0)
    tel = Component(user_id=u.id, kind="telescope", name="Scope",
                    aperture_mm=80, focal_length_mm=480)
    cam = Component(user_id=u.id, kind="camera", name="Cam",
                    sensor_width_mm=10, sensor_height_mm=8, pixel_size_um=3.76)
    db.add_all([loc, obj_row, tel, cam])
    db.flush()

    rig = Rig(user_id=u.id, rig_name=rig_name, telescope_id=tel.id, camera_id=cam.id)
    db.add(rig)
    sync_rig_links(db, rig)
    db.flush()

    framing = SavedFraming(user_id=u.id, object_name=obj, rig_id=rig.id, rig_name=rig_name)
    db.add(framing)
    sync_framing_links(db, framing)
    db.flush()

    proj = Project(id=f"p_{username}", user_id=u.id, name="P", target_object_name=obj)
    db.add(proj)
    sync_project_link(db, proj)
    db.flush()

    sess = JournalSession(user_id=u.id, date_utc=date(2025, 1, 1), object_name=obj,
                          location_name="Home", rig_id_snapshot=rig.id)
    db.add(sess)
    sync_session_links(db, sess)
    db.commit()

    return SimpleNamespace(u=u, loc=loc, obj=obj_row, tel=tel, cam=cam, rig=rig,
                           framing=framing, proj=proj, sess=sess)


def test_export_carries_every_record_and_link_uid(db_session):
    r = _populate(db_session, "uid_export_user")
    cfg, rigs, jdoc = export_user_data(db_session, r.u)

    # Own record UIDs.
    assert cfg["locations"]["Home"]["record_uid"] == r.loc.record_uid
    assert cfg["objects"][0]["record_uid"] == r.obj.record_uid
    rig_row = next(x for x in rigs["rigs"] if x["rig_name"] == "Main")
    assert rig_row["record_uid"] == r.rig.record_uid
    assert rigs["components"]["telescopes"][0]["record_uid"] == r.tel.record_uid
    assert rigs["components"]["cameras"][0]["record_uid"] == r.cam.record_uid

    # Link UIDs equal the DB values.
    assert rig_row["telescope_record_uid"] == r.rig.telescope_record_uid
    assert rig_row["camera_record_uid"] == r.rig.camera_record_uid
    fr = cfg["saved_framings"][0]
    assert fr["rig_record_uid"] == r.framing.rig_record_uid
    assert fr["object_record_uid"] == r.framing.object_record_uid
    s = jdoc["sessions"][0]
    assert s["object_record_uid"] == r.sess.object_record_uid
    assert s["location_record_uid"] == r.sess.location_record_uid
    assert s["rig_record_uid"] == r.sess.rig_record_uid
    assert jdoc["projects"][0]["target_object_record_uid"] == r.proj.target_object_record_uid

    assert cfg["format_version"] == 2
    assert rigs["format_version"] == 2
    assert jdoc["format_version"] == 2


# Old key sets (from the audit). The change is additive: each must stay a subset.
OLD_CONFIG_LOC = {"lat", "lon", "timezone", "altitude_threshold", "active",
                  "comments", "horizon_mask", "bortle_scale", "elevation", "sqm_zenith"}
OLD_FRAMING = {"object_name", "rig_name", "ra", "dec", "rotation", "survey",
               "blend_survey", "blend_opacity", "mosaic_cols", "mosaic_rows",
               "mosaic_overlap", "img_brightness", "img_contrast", "img_gamma",
               "img_saturation", "geo_belt_enabled"}
OLD_RIG = {"rig_id", "rig_name", "telescope_id", "telescope_name", "camera_id",
           "camera_name", "reducer_extender_id", "reducer_extender_name",
           "effective_focal_length", "f_ratio", "image_scale", "fov_w_arcmin",
           "guide_telescope_id", "guide_telescope_name", "guide_camera_id",
           "guide_camera_name", "guide_is_oag"}
OLD_SESSION = {"date", "session_date", "object_name", "target_object_id", "notes",
               "general_notes_problems_learnings", "session_id", "project_id",
               "project_name", "project_ids", "session_image_file", "rig_id_snapshot",
               "custom_filter_data", "asiair_log_content", "phd2_log_content",
               "nina_log_content", "log_analysis_cache"}


def test_export_key_parity_with_old_exporters(client, db_session):
    _populate(db_session, "default", obj="M31")

    cfg = yaml.safe_load(client.get("/download_config").data)
    rigs = yaml.safe_load(client.get("/download_rig_config").data)
    jdoc = yaml.safe_load(client.get("/download_journal").data)

    assert OLD_CONFIG_LOC <= set(cfg["locations"]["Home"])
    assert OLD_FRAMING <= set(cfg["saved_framings"][0])
    assert OLD_RIG <= set(rigs["rigs"][0])
    assert OLD_SESSION <= set(jdoc["sessions"][0])


def test_export_writes_active_for_every_rig(db_session):
    r = _populate(db_session, "active_export_user")
    off = Rig(user_id=r.u.id, rig_name="Packed", telescope_id=r.tel.id,
              camera_id=r.cam.id, active=False)
    db_session.add(off)
    sync_rig_links(db_session, off)
    db_session.commit()

    _cfg, rigs, _jdoc = export_user_data(db_session, r.u)
    by_name = {row["rig_name"]: row for row in rigs["rigs"]}

    assert by_name["Main"]["active"] is True
    assert by_name["Packed"]["active"] is False
    assert all(isinstance(row["active"], bool) for row in rigs["rigs"])


def test_export_never_leaks_another_user(db_session):
    r = _populate(db_session, "owner")
    other = _user(db_session, "intruder")
    o_loc = Location(user_id=other.id, name="Secret Loc", lat=9.0, lon=9.0, timezone="UTC")
    o_rig = Rig(user_id=other.id, rig_name="Secret Rig")
    o_obj = AstroObject(user_id=other.id, object_name="SECRET", ra_hours=1.0, dec_deg=1.0)
    db_session.add_all([o_loc, o_rig, o_obj])
    db_session.commit()

    cfg, rigs, jdoc = export_user_data(db_session, r.u)
    blob = repr(cfg) + repr(rigs) + repr(jdoc)
    for secret in ("Secret Loc", "Secret Rig", "SECRET",
                   o_loc.record_uid, o_rig.record_uid, o_obj.record_uid):
        assert secret not in blob


def test_export_zip_contains_own_rigs_file(multi_user_client, monkeypatch):
    client, _ids = multi_user_client
    monkeypatch.setattr('nova.blueprints.tools.SINGLE_USER_MODE', False)

    resp = client.get("/tools/export/UserA")
    assert resp.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(resp.data)).namelist()
    assert "rigs_UserA.yaml" in names
    assert "rigs_default.yaml" not in names


def test_component_role_with_unknown_uid_exports_name_none(db_session):
    r = _populate(db_session, "unknown_uid_user")
    # A role whose UID matches no component: name is None, the stored id and
    # UID are still written as they are.
    r.rig.telescope_record_uid = "0" * 32
    db_session.commit()

    _cfg, rigs, _jdoc = export_user_data(db_session, r.u)
    row = rigs["rigs"][0]
    assert row["telescope_record_uid"] == "0" * 32
    assert row["telescope_id"] == r.tel.id
    assert row["telescope_name"] is None
    assert row["camera_name"] == "Cam"


def test_current_importer_accepts_new_files(db_session, tmp_path):
    _populate(db_session, "rt_source")
    assert export_user_to_yaml("rt_source", out_dir=str(tmp_path)) is True

    cfg = yaml.safe_load((tmp_path / "config_rt_source.yaml").read_text())
    rigs = yaml.safe_load((tmp_path / "rigs_rt_source.yaml").read_text())
    jdoc = yaml.safe_load((tmp_path / "journal_rt_source.yaml").read_text())

    target = _user(db_session, "rt_target")
    _migrate_locations(db_session, target, cfg)
    _migrate_objects(db_session, target, cfg)
    _migrate_components_and_rigs(db_session, target, rigs, target.username)
    _migrate_saved_framings(db_session, target, cfg)
    _migrate_journal(db_session, target, jdoc)
    db_session.commit()

    assert db_session.query(Location).filter_by(user_id=target.id, name="Home").one()
    assert db_session.query(AstroObject).filter_by(user_id=target.id, object_name="M42").one()
    assert db_session.query(Rig).filter_by(user_id=target.id, rig_name="Main").one()


def test_import_config_route_accepts_new_format(client, db_session, tmp_path, monkeypatch):
    monkeypatch.setattr('nova.update_outlook_cache', lambda *a, **k: None)
    _populate(db_session, "default", obj="M31")
    assert export_user_to_yaml("default", out_dir=str(tmp_path)) is True
    cfg_text = (tmp_path / "config_default.yaml").read_text()

    resp = client.post('/import_config',
                       data={'file': (io.BytesIO(cfg_text.encode()), 'config.yaml')},
                       content_type='multipart/form-data')
    assert resp.status_code == 302

    db_session.expire_all()
    u = db_session.query(DbUser).filter_by(username="default").one()
    # The data is imported as before ...
    assert db_session.query(Location).filter_by(user_id=u.id, name="Home").one()
    assert db_session.query(AstroObject).filter_by(user_id=u.id, object_name="M31").one()

    # ... and the UiPref blob keeps only the six general settings.
    prefs = db_session.query(UiPref).filter_by(user_id=u.id).one()
    blob = json.loads(prefs.json_blob)
    for forbidden in ("format_version", "record_uid", "locations", "objects",
                      "saved_framings", "saved_views"):
        assert forbidden not in blob
