"""
Nova DSO Tracker — REST API v1

Full CRUD API for all data models, secured by API-key authentication.
Prefix: /api/v1  (set during blueprint registration)
"""

from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Blueprint, request, jsonify, g
import uuid

from nova.api_auth import (
    api_key_required,
    api_key_or_login_required,
    create_api_key,
    hash_api_key,
    key_prefix as _key_prefix,
)
from nova.permissions import api_admin_required, api_permission_required
from sqlalchemy.orm import selectinload
from nova.models import (
    SessionLocal,
    DbUser,
    AstroObject,
    Project,
    Location,
    HorizonPoint,
    SavedFraming,
    Component,
    Rig,
    JournalSession,
    SavedView,
    UserCustomFilter,
    ApiKey,
    UiPref,
    Role,
    Permission,
    BlogPost,
    BlogImage,
    BlogComment,
)
from nova.config import SINGLE_USER_MODE, BLOG_UPLOAD_FOLDER
from nova.helpers import (
    _save_blog_image,
    _delete_blog_image_files,
    BLOG_COMMENT_MAX_LEN,
)

rest_api_bp = Blueprint("rest_api", __name__)

# ──────────────────────────────────────────────────────────
#  Helpers
# ──────────────────────────────────────────────────────────


def _db():
    """Return the scoped SQLAlchemy session registry.

    Call .remove() to release the session back to the pool.
    """
    return SessionLocal


def _paginate(query):
    """Apply pagination from query-string ?page=&per_page= and return (items, meta)."""
    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(200, max(1, request.args.get("per_page", 50, type=int)))
    total = query.count()
    items = query.offset((page - 1) * per_page).limit(per_page).all()
    return items, {
        "page": page,
        "per_page": per_page,
        "total": total,
        "pages": max(1, (total + per_page - 1) // per_page),
    }


def _ok(data, meta=None, status=200):
    body = {"data": data}
    if meta is not None:
        body["meta"] = meta
    return jsonify(body), status


def _err(message, status=400):
    return jsonify({"error": message}), status


def _user_id():
    """Return the authenticated DbUser.id."""
    return g.db_user.id


def _to_date(val):
    """Parse a date string (YYYY-MM-DD) or return None."""
    if val is None:
        return None
    if isinstance(val, str):
        try:
            return datetime.strptime(val, "%Y-%m-%d").date()
        except ValueError:
            return None
    return val


def _to_float(val):
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _to_int(val):
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _to_bool(val):
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.lower() in ("true", "1", "yes")
    return bool(val)


# ──────────────────────────────────────────────────────────
#  Serializers
# ──────────────────────────────────────────────────────────


def _serialize_object(obj):
    return {
        "id": obj.id,
        "object_name": obj.object_name,
        "common_name": obj.common_name,
        "ra_hours": obj.ra_hours,
        "dec_deg": obj.dec_deg,
        "type": obj.type,
        "constellation": obj.constellation,
        "magnitude": obj.magnitude,
        "size": obj.size,
        "sb": obj.sb,
        "active_project": obj.active_project,
        "project_name": obj.project_name,
        "is_shared": obj.is_shared,
        "shared_notes": obj.shared_notes,
        "catalog_sources": obj.catalog_sources,
        "catalog_info": obj.catalog_info,
        "enabled": obj.enabled,
        "image_url": obj.image_url,
        "image_credit": obj.image_credit,
        "image_source_link": obj.image_source_link,
        "description_text": obj.description_text,
        "description_credit": obj.description_credit,
        "description_source_link": obj.description_source_link,
    }


def _serialize_project(p):
    return {
        "id": p.id,
        "name": p.name,
        "target_object_name": p.target_object_name,
        "description_notes": p.description_notes,
        "framing_notes": p.framing_notes,
        "processing_notes": p.processing_notes,
        "final_image_file": p.final_image_file,
        "goals": p.goals,
        "status": p.status,
    }


def _serialize_location(loc):
    return {
        "id": loc.id,
        "stable_uid": loc.stable_uid,
        "name": loc.name,
        "lat": loc.lat,
        "lon": loc.lon,
        "timezone": loc.timezone,
        "altitude_threshold": loc.altitude_threshold,
        "is_default": loc.is_default,
        "active": loc.active,
        "comments": loc.comments,
    }


def _serialize_horizon_point(hp):
    return {
        "id": hp.id,
        "az_deg": hp.az_deg,
        "alt_min_deg": hp.alt_min_deg,
    }


def _serialize_component(c):
    return {
        "id": c.id,
        "stable_uid": c.stable_uid,
        "kind": c.kind,
        "name": c.name,
        "aperture_mm": c.aperture_mm,
        "focal_length_mm": c.focal_length_mm,
        "sensor_width_mm": c.sensor_width_mm,
        "sensor_height_mm": c.sensor_height_mm,
        "pixel_size_um": c.pixel_size_um,
        "factor": c.factor,
        "is_shared": c.is_shared,
    }


def _serialize_rig(r):
    return {
        "id": r.id,
        "stable_uid": r.stable_uid,
        "rig_name": r.rig_name,
        "telescope_id": r.telescope_id,
        "camera_id": r.camera_id,
        "reducer_extender_id": r.reducer_extender_id,
        "effective_focal_length": r.effective_focal_length,
        "f_ratio": r.f_ratio,
        "image_scale": r.image_scale,
        "fov_w_arcmin": r.fov_w_arcmin,
        "guide_telescope_id": r.guide_telescope_id,
        "guide_camera_id": r.guide_camera_id,
        "guide_is_oag": r.guide_is_oag,
    }


def _serialize_session(s):
    return {
        "id": s.id,
        "project_id": s.project_id,
        "date_utc": s.date_utc.isoformat() if s.date_utc else None,
        "object_name": s.object_name,
        "notes": s.notes,
        "session_image_file": s.session_image_file,
        "location_name": s.location_name,
        "seeing": s.seeing_observed_fwhm,
        "moon_phase_pct": s.moon_illumination_session,
        "moon_proximity_deg": s.moon_angular_separation_session,
        "weather": s.weather_notes,
        "sqm": s.sky_sqm_observed,
        "filter_type": s.filter_used_session,
        "guiding_rms": s.guiding_rms_avg_arcsec,
        "acquisition_software": s.acquisition_software,
        "gain": s.gain_setting,
        "offset": s.offset_setting,
        "camera_temp_c": s.camera_temp_setpoint_c,
        "binning": s.binning_session,
        "dark_frames": s.darks_strategy,
        "flat_frames": s.flats_strategy,
        "bias_frames": s.bias_darkflats_strategy,
        "rating": s.session_rating_subjective,
        "transparency": s.transparency_observed_scale,
        "l_subs": s.filter_L_subs,
        "l_exposure": s.filter_L_exposure_sec,
        "r_subs": s.filter_R_subs,
        "r_exposure": s.filter_R_exposure_sec,
        "g_subs": s.filter_G_subs,
        "g_exposure": s.filter_G_exposure_sec,
        "b_subs": s.filter_B_subs,
        "b_exposure": s.filter_B_exposure_sec,
        "ha_subs": s.filter_Ha_subs,
        "ha_exposure": s.filter_Ha_exposure_sec,
        "oiii_subs": s.filter_OIII_subs,
        "oiii_exposure": s.filter_OIII_exposure_sec,
        "sii_subs": s.filter_SII_subs,
        "sii_exposure": s.filter_SII_exposure_sec,
        "custom_filter_data": s.custom_filter_data,
        "calculated_integration_time_minutes": s.calculated_integration_time_minutes,
        "rig_snapshot_telescope": s.telescope_name_snapshot,
        "rig_snapshot_camera": s.camera_name_snapshot,
        "rig_snapshot_reducer": s.reducer_name_snapshot,
        "rig_snapshot_efl": s.rig_efl_snapshot,
        "rig_snapshot_fratio": s.rig_fr_snapshot,
        "rig_snapshot_image_scale": s.rig_scale_snapshot,
        "rig_snapshot_fov_w": s.rig_fov_w_snapshot,
        "rig_snapshot_fov_h": s.rig_fov_h_snapshot,
        "external_id": s.external_id,
    }


def _serialize_saved_view(v):
    return {
        "id": v.id,
        "name": v.name,
        "description": v.description,
        "settings_json": v.settings_json,
        "is_shared": v.is_shared,
    }


def _serialize_framing(f):
    return {
        "id": f.id,
        "object_name": f.object_name,
        "rig_id": f.rig_id,
        "survey_name": f.survey,
        "survey_ra_hours": f.ra,
        "survey_dec_deg": f.dec,
        "survey_rotation_deg": f.rotation,
        "mosaic_panels_x": f.mosaic_cols,
        "mosaic_panels_y": f.mosaic_rows,
        "mosaic_overlap_pct": f.mosaic_overlap,
        "image_brightness": f.img_brightness,
        "image_contrast": f.img_contrast,
        "image_saturation": f.img_saturation,
        "geo_belt_enabled": f.geo_belt_enabled,
        "updated_at": f.updated_at.isoformat() if f.updated_at else None,
    }


def _serialize_custom_filter(cf):
    return {
        "id": cf.id,
        "filter_key": cf.filter_key,
        "filter_label": cf.filter_label,
        "created_at": cf.created_at.isoformat() if cf.created_at else None,
    }


def _serialize_api_key(k):
    return {
        "id": k.id,
        "key_prefix": k.key_prefix,
        "name": k.name,
        "created_at": k.created_at.isoformat() if k.created_at else None,
        "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
        "is_active": k.is_active,
    }


def _serialize_ui_pref(p):
    return {
        "id": p.id,
        "json_blob": p.json_blob,
    }


# ──────────────────────────────────────────────────────────
#  Blog Serializers
# ──────────────────────────────────────────────────────────


def _serialize_blog_image(img, user_id: int) -> dict:
    """Serialize a BlogImage to dict with constructed URLs."""
    return {
        "id": img.id,
        "post_id": img.post_id,
        "filename": img.filename,
        "thumb_filename": img.thumb_filename,
        "caption": img.caption,
        "display_order": img.display_order,
        "image_url": f"/blog/uploads/{user_id}/{img.filename}",
        "thumb_url": f"/blog/uploads/{user_id}/{img.thumb_filename}"
        if img.thumb_filename
        else None,
    }


def _serialize_blog_comment(c) -> dict:
    """Serialize a BlogComment to dict with username."""
    return {
        "id": c.id,
        "post_id": c.post_id,
        "user_id": c.user_id,
        "username": c.user.username if c.user else None,
        "content": c.content,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def _serialize_blog_post(post, include_full: bool = False) -> dict:
    """
    Serialize a BlogPost to dict.

    Args:
        post: BlogPost instance (with images and optionally comments loaded)
        include_full: If True, include comments; if False, omit comments (for list view)
    """
    base = {
        "id": post.id,
        "user_id": post.user_id,
        "username": post.user.username if post.user else None,
        "title": post.title,
        "content": post.content,
        "created_at": post.created_at.isoformat() if post.created_at else None,
        "updated_at": post.updated_at.isoformat() if post.updated_at else None,
        "images": [_serialize_blog_image(img, post.user_id) for img in post.images],
    }
    if include_full:
        base["comments"] = [_serialize_blog_comment(c) for c in post.comments]
    return base


def _apply_blog_post_fields(post, data: dict) -> None:
    """Apply title and content from data dict to BlogPost (partial updates OK)."""
    if "title" in data:
        title = str(data["title"])[:256]  # enforce max length
        post.title = title
    if "content" in data:
        post.content = str(data["content"])


# ──────────────────────────────────────────────────────────
#  OBJECTS  (AstroObject)
# ──────────────────────────────────────────────────────────


@rest_api_bp.route("/objects", methods=["GET"])
@api_key_required
@api_permission_required("objects.view")
def list_objects():
    """List all objects for the authenticated user. Supports pagination and filtering."""
    db = _db()
    try:
        q = db.query(AstroObject).filter(AstroObject.user_id == _user_id())

        # Optional filters
        obj_type = request.args.get("type")
        if obj_type:
            q = q.filter(AstroObject.type == obj_type)
        constellation = request.args.get("constellation")
        if constellation:
            q = q.filter(AstroObject.constellation == constellation)
        enabled = request.args.get("enabled")
        if enabled is not None:
            q = q.filter(AstroObject.enabled == _to_bool(enabled))
        search = request.args.get("search")
        if search:
            pattern = f"%{search}%"
            q = q.filter(
                (AstroObject.object_name.ilike(pattern))
                | (AstroObject.common_name.ilike(pattern))
            )

        q = q.order_by(AstroObject.object_name)
        items, meta = _paginate(q)
        return _ok([_serialize_object(o) for o in items], meta)
    finally:
        db.remove()


@rest_api_bp.route("/objects", methods=["POST"])
@api_key_required
@api_permission_required("objects.create")
def create_object():
    """Create a new astronomical object."""
    data = request.get_json(silent=True) or {}
    if not data.get("object_name"):
        return _err("object_name is required")

    db = _db()
    try:
        existing = (
            db.query(AstroObject)
            .filter(
                AstroObject.user_id == _user_id(),
                AstroObject.object_name == data["object_name"],
            )
            .first()
        )
        if existing:
            return _err(f"Object '{data['object_name']}' already exists", 409)

        obj = AstroObject(user_id=_user_id())
        _apply_object_fields(obj, data)
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return _ok(_serialize_object(obj), status=201)
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


@rest_api_bp.route("/objects/<int:object_id>", methods=["GET"])
@api_key_required
@api_permission_required("objects.view")
def get_object(object_id):
    db = _db()
    try:
        obj = (
            db.query(AstroObject)
            .filter(
                AstroObject.id == object_id,
                AstroObject.user_id == _user_id(),
            )
            .first()
        )
        if obj is None:
            return _err("Object not found", 404)
        return _ok(_serialize_object(obj))
    finally:
        db.remove()


@rest_api_bp.route("/objects/<int:object_id>", methods=["PUT"])
@api_key_required
@api_permission_required("objects.edit")
def update_object(object_id):
    data = request.get_json(silent=True) or {}
    db = _db()
    try:
        obj = (
            db.query(AstroObject)
            .filter(
                AstroObject.id == object_id,
                AstroObject.user_id == _user_id(),
            )
            .first()
        )
        if obj is None:
            return _err("Object not found", 404)
        _apply_object_fields(obj, data)
        db.commit()
        db.refresh(obj)
        return _ok(_serialize_object(obj))
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


@rest_api_bp.route("/objects/<int:object_id>", methods=["DELETE"])
@api_key_required
@api_permission_required("objects.delete")
def delete_object(object_id):
    db = _db()
    try:
        obj = (
            db.query(AstroObject)
            .filter(
                AstroObject.id == object_id,
                AstroObject.user_id == _user_id(),
            )
            .first()
        )
        if obj is None:
            return _err("Object not found", 404)
        db.delete(obj)
        db.commit()
        return _ok({"deleted": True})
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


def _apply_object_fields(obj, data):
    """Apply writable fields from request data to an AstroObject."""
    _fields = [
        "object_name",
        "common_name",
        "ra_hours",
        "dec_deg",
        "type",
        "constellation",
        "magnitude",
        "size",
        "sb",
        "active_project",
        "project_name",
        "is_shared",
        "shared_notes",
        "catalog_sources",
        "catalog_info",
        "enabled",
        "image_url",
        "image_credit",
        "image_source_link",
        "description_text",
        "description_credit",
        "description_source_link",
    ]
    for f in _fields:
        if f in data:
            setattr(obj, f, data[f])


# ──────────────────────────────────────────────────────────
#  PROJECTS
# ──────────────────────────────────────────────────────────


@rest_api_bp.route("/projects", methods=["GET"])
@api_key_required
@api_permission_required("projects.view")
def list_projects():
    db = _db()
    try:
        q = db.query(Project).filter(Project.user_id == _user_id())
        status = request.args.get("status")
        if status:
            q = q.filter(Project.status == status)
        q = q.order_by(Project.name)
        items, meta = _paginate(q)
        return _ok([_serialize_project(p) for p in items], meta)
    finally:
        db.remove()


@rest_api_bp.route("/projects", methods=["POST"])
@api_key_required
@api_permission_required("projects.create")
def create_project():
    data = request.get_json(silent=True) or {}
    if not data.get("name"):
        return _err("name is required")
    db = _db()
    try:
        project_id = data.get("id") or str(uuid.uuid4())[:8]
        existing = db.query(Project).filter(Project.id == project_id).first()
        if existing:
            return _err(f"Project with id '{project_id}' already exists", 409)
        p = Project(id=project_id, user_id=_user_id())
        _apply_project_fields(p, data)
        db.add(p)
        db.commit()
        db.refresh(p)
        return _ok(_serialize_project(p), status=201)
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


@rest_api_bp.route("/projects/<string:project_id>", methods=["GET"])
@api_key_required
@api_permission_required("projects.view")
def get_project(project_id):
    db = _db()
    try:
        p = (
            db.query(Project)
            .filter(
                Project.id == project_id,
                Project.user_id == _user_id(),
            )
            .first()
        )
        if p is None:
            return _err("Project not found", 404)
        return _ok(_serialize_project(p))
    finally:
        db.remove()


@rest_api_bp.route("/projects/<string:project_id>", methods=["PUT"])
@api_key_required
@api_permission_required("projects.edit")
def update_project(project_id):
    data = request.get_json(silent=True) or {}
    db = _db()
    try:
        p = (
            db.query(Project)
            .filter(
                Project.id == project_id,
                Project.user_id == _user_id(),
            )
            .first()
        )
        if p is None:
            return _err("Project not found", 404)
        _apply_project_fields(p, data)
        db.commit()
        db.refresh(p)
        return _ok(_serialize_project(p))
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


@rest_api_bp.route("/projects/<string:project_id>", methods=["DELETE"])
@api_key_required
@api_permission_required("projects.delete")
def delete_project(project_id):
    db = _db()
    try:
        p = (
            db.query(Project)
            .filter(
                Project.id == project_id,
                Project.user_id == _user_id(),
            )
            .first()
        )
        if p is None:
            return _err("Project not found", 404)
        db.delete(p)
        db.commit()
        return _ok({"deleted": True})
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


def _apply_project_fields(p, data):
    for f in [
        "name",
        "target_object_name",
        "description_notes",
        "framing_notes",
        "processing_notes",
        "final_image_file",
        "goals",
        "status",
    ]:
        if f in data:
            setattr(p, f, data[f])


# ──────────────────────────────────────────────────────────
#  LOCATIONS
# ──────────────────────────────────────────────────────────


@rest_api_bp.route("/locations", methods=["GET"])
@api_key_required
@api_permission_required("locations.view")
def list_locations():
    db = _db()
    try:
        q = db.query(Location).filter(Location.user_id == _user_id())
        q = q.order_by(Location.name)
        items, meta = _paginate(q)
        return _ok([_serialize_location(loc) for loc in items], meta)
    finally:
        db.remove()


@rest_api_bp.route("/locations", methods=["POST"])
@api_key_required
@api_permission_required("locations.create")
def create_location():
    data = request.get_json(silent=True) or {}
    if not data.get("name"):
        return _err("name is required")
    db = _db()
    try:
        loc = Location(
            stable_uid=data.get("stable_uid") or str(uuid.uuid4()),
            user_id=_user_id(),
        )
        _apply_location_fields(loc, data)
        db.add(loc)
        db.commit()
        db.refresh(loc)
        return _ok(_serialize_location(loc), status=201)
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


@rest_api_bp.route("/locations/<int:location_id>", methods=["GET"])
@api_key_required
@api_permission_required("locations.view")
def get_location(location_id):
    db = _db()
    try:
        loc = (
            db.query(Location)
            .filter(
                Location.id == location_id,
                Location.user_id == _user_id(),
            )
            .first()
        )
        if loc is None:
            return _err("Location not found", 404)
        result = _serialize_location(loc)
        result["horizon_points"] = [
            _serialize_horizon_point(hp) for hp in loc.horizon_points
        ]
        return _ok(result)
    finally:
        db.remove()


@rest_api_bp.route("/locations/<int:location_id>", methods=["PUT"])
@api_key_required
@api_permission_required("locations.edit")
def update_location(location_id):
    data = request.get_json(silent=True) or {}
    db = _db()
    try:
        loc = (
            db.query(Location)
            .filter(
                Location.id == location_id,
                Location.user_id == _user_id(),
            )
            .first()
        )
        if loc is None:
            return _err("Location not found", 404)
        _apply_location_fields(loc, data)
        db.commit()
        db.refresh(loc)
        return _ok(_serialize_location(loc))
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


@rest_api_bp.route("/locations/<int:location_id>", methods=["DELETE"])
@api_key_required
@api_permission_required("locations.delete")
def delete_location(location_id):
    db = _db()
    try:
        loc = (
            db.query(Location)
            .filter(
                Location.id == location_id,
                Location.user_id == _user_id(),
            )
            .first()
        )
        if loc is None:
            return _err("Location not found", 404)
        db.delete(loc)
        db.commit()
        return _ok({"deleted": True})
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


def _apply_location_fields(loc, data):
    for f in [
        "name",
        "lat",
        "lon",
        "timezone",
        "altitude_threshold",
        "is_default",
        "active",
        "comments",
    ]:
        if f in data:
            setattr(loc, f, data[f])


# ──── Horizon points (sub-resource) ────


@rest_api_bp.route("/locations/<int:location_id>/horizon", methods=["GET"])
@api_key_required
@api_permission_required("locations.view")
def get_horizon(location_id):
    db = _db()
    try:
        loc = (
            db.query(Location)
            .filter(
                Location.id == location_id,
                Location.user_id == _user_id(),
            )
            .first()
        )
        if loc is None:
            return _err("Location not found", 404)
        points = (
            db.query(HorizonPoint)
            .filter(
                HorizonPoint.location_id == loc.id,
            )
            .order_by(HorizonPoint.az_deg)
            .all()
        )
        return _ok([_serialize_horizon_point(hp) for hp in points])
    finally:
        db.remove()


@rest_api_bp.route("/locations/<int:location_id>/horizon", methods=["PUT"])
@api_key_required
@api_permission_required("locations.edit")
def set_horizon(location_id):
    """Replace ALL horizon points for a location."""
    data = request.get_json(silent=True) or {}
    points_data = data.get("points", [])
    db = _db()
    try:
        loc = (
            db.query(Location)
            .filter(
                Location.id == location_id,
                Location.user_id == _user_id(),
            )
            .first()
        )
        if loc is None:
            return _err("Location not found", 404)
        # Delete existing
        db.query(HorizonPoint).filter(HorizonPoint.location_id == loc.id).delete()
        # Insert new
        for pt in points_data:
            hp = HorizonPoint(
                location_id=loc.id,
                az_deg=pt.get("az_deg"),
                alt_min_deg=pt.get("alt_min_deg"),
            )
            db.add(hp)
        db.commit()
        new_points = (
            db.query(HorizonPoint)
            .filter(
                HorizonPoint.location_id == loc.id,
            )
            .order_by(HorizonPoint.az_deg)
            .all()
        )
        return _ok([_serialize_horizon_point(hp) for hp in new_points])
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


# ──────────────────────────────────────────────────────────
#  COMPONENTS  (telescopes, cameras, reducer/extenders)
# ──────────────────────────────────────────────────────────


@rest_api_bp.route("/components", methods=["GET"])
@api_key_required
@api_permission_required("equipment.view")
def list_components():
    db = _db()
    try:
        q = db.query(Component).filter(Component.user_id == _user_id())
        kind = request.args.get("kind")
        if kind:
            q = q.filter(Component.kind == kind)
        q = q.order_by(Component.kind, Component.name)
        items, meta = _paginate(q)
        return _ok([_serialize_component(c) for c in items], meta)
    finally:
        db.remove()


@rest_api_bp.route("/components", methods=["POST"])
@api_key_required
@api_permission_required("equipment.create")
def create_component():
    data = request.get_json(silent=True) or {}
    if not data.get("kind") or not data.get("name"):
        return _err("kind and name are required")
    if data["kind"] not in ("telescope", "camera", "reducer_extender"):
        return _err("kind must be 'telescope', 'camera', or 'reducer_extender'")
    db = _db()
    try:
        c = Component(
            stable_uid=data.get("stable_uid") or str(uuid.uuid4()),
            user_id=_user_id(),
        )
        _apply_component_fields(c, data)
        db.add(c)
        db.commit()
        db.refresh(c)
        return _ok(_serialize_component(c), status=201)
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


@rest_api_bp.route("/components/<int:component_id>", methods=["GET"])
@api_key_required
@api_permission_required("equipment.view")
def get_component(component_id):
    db = _db()
    try:
        c = (
            db.query(Component)
            .filter(
                Component.id == component_id,
                Component.user_id == _user_id(),
            )
            .first()
        )
        if c is None:
            return _err("Component not found", 404)
        return _ok(_serialize_component(c))
    finally:
        db.remove()


@rest_api_bp.route("/components/<int:component_id>", methods=["PUT"])
@api_key_required
@api_permission_required("equipment.edit")
def update_component(component_id):
    data = request.get_json(silent=True) or {}
    db = _db()
    try:
        c = (
            db.query(Component)
            .filter(
                Component.id == component_id,
                Component.user_id == _user_id(),
            )
            .first()
        )
        if c is None:
            return _err("Component not found", 404)
        _apply_component_fields(c, data)
        db.commit()
        db.refresh(c)
        return _ok(_serialize_component(c))
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


@rest_api_bp.route("/components/<int:component_id>", methods=["DELETE"])
@api_key_required
@api_permission_required("equipment.delete")
def delete_component(component_id):
    db = _db()
    try:
        c = (
            db.query(Component)
            .filter(
                Component.id == component_id,
                Component.user_id == _user_id(),
            )
            .first()
        )
        if c is None:
            return _err("Component not found", 404)
        db.delete(c)
        db.commit()
        return _ok({"deleted": True})
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


def _apply_component_fields(c, data):
    for f in [
        "kind",
        "name",
        "aperture_mm",
        "focal_length_mm",
        "sensor_width_mm",
        "sensor_height_mm",
        "pixel_size_um",
        "factor",
        "is_shared",
    ]:
        if f in data:
            setattr(c, f, data[f])


# ──────────────────────────────────────────────────────────
#  RIGS
# ──────────────────────────────────────────────────────────


@rest_api_bp.route("/rigs", methods=["GET"])
@api_key_required
@api_permission_required("equipment.view")
def list_rigs():
    db = _db()
    try:
        q = db.query(Rig).filter(Rig.user_id == _user_id())
        q = q.order_by(Rig.rig_name)
        items, meta = _paginate(q)
        return _ok([_serialize_rig(r) for r in items], meta)
    finally:
        db.remove()


@rest_api_bp.route("/rigs", methods=["POST"])
@api_key_required
@api_permission_required("equipment.create")
def create_rig():
    data = request.get_json(silent=True) or {}
    if not data.get("rig_name"):
        return _err("rig_name is required")
    db = _db()
    try:
        r = Rig(
            stable_uid=data.get("stable_uid") or str(uuid.uuid4()),
            user_id=_user_id(),
        )
        _apply_rig_fields(r, data)
        db.add(r)
        db.commit()
        db.refresh(r)
        return _ok(_serialize_rig(r), status=201)
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


@rest_api_bp.route("/rigs/<int:rig_id>", methods=["GET"])
@api_key_required
@api_permission_required("equipment.view")
def get_rig(rig_id):
    db = _db()
    try:
        r = (
            db.query(Rig)
            .filter(
                Rig.id == rig_id,
                Rig.user_id == _user_id(),
            )
            .first()
        )
        if r is None:
            return _err("Rig not found", 404)
        return _ok(_serialize_rig(r))
    finally:
        db.remove()


@rest_api_bp.route("/rigs/<int:rig_id>", methods=["PUT"])
@api_key_required
@api_permission_required("equipment.edit")
def update_rig(rig_id):
    data = request.get_json(silent=True) or {}
    db = _db()
    try:
        r = (
            db.query(Rig)
            .filter(
                Rig.id == rig_id,
                Rig.user_id == _user_id(),
            )
            .first()
        )
        if r is None:
            return _err("Rig not found", 404)
        _apply_rig_fields(r, data)
        db.commit()
        db.refresh(r)
        return _ok(_serialize_rig(r))
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


@rest_api_bp.route("/rigs/<int:rig_id>", methods=["DELETE"])
@api_key_required
@api_permission_required("equipment.delete")
def delete_rig(rig_id):
    db = _db()
    try:
        r = (
            db.query(Rig)
            .filter(
                Rig.id == rig_id,
                Rig.user_id == _user_id(),
            )
            .first()
        )
        if r is None:
            return _err("Rig not found", 404)
        db.delete(r)
        db.commit()
        return _ok({"deleted": True})
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


def _apply_rig_fields(r, data):
    for f in [
        "rig_name",
        "telescope_id",
        "camera_id",
        "reducer_extender_id",
        "effective_focal_length",
        "f_ratio",
        "image_scale",
        "fov_w_arcmin",
        "guide_telescope_id",
        "guide_camera_id",
        "guide_is_oag",
    ]:
        if f in data:
            setattr(r, f, data[f])


# ──────────────────────────────────────────────────────────
#  JOURNAL SESSIONS
# ──────────────────────────────────────────────────────────


@rest_api_bp.route("/sessions", methods=["GET"])
@api_key_required
@api_permission_required("journal.view")
def list_sessions():
    db = _db()
    try:
        q = db.query(JournalSession).filter(JournalSession.user_id == _user_id())
        obj = request.args.get("object_name")
        if obj:
            q = q.filter(JournalSession.object_name == obj)
        project = request.args.get("project_id")
        if project:
            q = q.filter(JournalSession.project_id == project)
        date_from = request.args.get("date_from")
        if date_from:
            d = _to_date(date_from)
            if d:
                q = q.filter(JournalSession.date_utc >= d)
        date_to = request.args.get("date_to")
        if date_to:
            d = _to_date(date_to)
            if d:
                q = q.filter(JournalSession.date_utc <= d)
        q = q.order_by(JournalSession.date_utc.desc())
        items, meta = _paginate(q)
        return _ok([_serialize_session(s) for s in items], meta)
    finally:
        db.remove()


@rest_api_bp.route("/sessions", methods=["POST"])
@api_key_required
@api_permission_required("journal.create")
def create_session():
    data = request.get_json(silent=True) or {}
    if not data.get("object_name"):
        return _err("object_name is required")
    db = _db()
    try:
        s = JournalSession(user_id=_user_id())
        _apply_session_fields(s, data)
        db.add(s)
        db.commit()
        db.refresh(s)
        return _ok(_serialize_session(s), status=201)
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


@rest_api_bp.route("/sessions/<int:session_id>", methods=["GET"])
@api_key_required
@api_permission_required("journal.view")
def get_session(session_id):
    db = _db()
    try:
        s = (
            db.query(JournalSession)
            .filter(
                JournalSession.id == session_id,
                JournalSession.user_id == _user_id(),
            )
            .first()
        )
        if s is None:
            return _err("Session not found", 404)
        return _ok(_serialize_session(s))
    finally:
        db.remove()


@rest_api_bp.route("/sessions/<int:session_id>", methods=["PUT"])
@api_key_required
@api_permission_required("journal.edit")
def update_session(session_id):
    data = request.get_json(silent=True) or {}
    db = _db()
    try:
        s = (
            db.query(JournalSession)
            .filter(
                JournalSession.id == session_id,
                JournalSession.user_id == _user_id(),
            )
            .first()
        )
        if s is None:
            return _err("Session not found", 404)
        _apply_session_fields(s, data)
        db.commit()
        db.refresh(s)
        return _ok(_serialize_session(s))
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


@rest_api_bp.route("/sessions/<int:session_id>", methods=["DELETE"])
@api_key_required
@api_permission_required("journal.delete")
def delete_session(session_id):
    db = _db()
    try:
        s = (
            db.query(JournalSession)
            .filter(
                JournalSession.id == session_id,
                JournalSession.user_id == _user_id(),
            )
            .first()
        )
        if s is None:
            return _err("Session not found", 404)
        db.delete(s)
        db.commit()
        return _ok({"deleted": True})
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


def _apply_session_fields(s, data):
    # Date needs special handling
    if "date_utc" in data:
        s.date_utc = _to_date(data["date_utc"])

    simple_str = [
        "object_name",
        "notes",
        "session_image_file",
        "location_name",
        "acquisition_software",
        "external_id",
    ]
    for f in simple_str:
        if f in data:
            setattr(s, f, data[f])

    str_mapped_fields = {
        "weather": "weather_notes",
        "filter_type": "filter_used_session",
        "binning": "binning_session",
        "dark_frames": "darks_strategy",
        "flat_frames": "flats_strategy",
        "bias_frames": "bias_darkflats_strategy",
        "transparency": "transparency_observed_scale",
        "rig_snapshot_telescope": "telescope_name_snapshot",
        "rig_snapshot_camera": "camera_name_snapshot",
        "rig_snapshot_reducer": "reducer_name_snapshot",
    }
    for json_field, model_field in str_mapped_fields.items():
        if json_field in data:
            setattr(s, model_field, data[json_field])

    float_mapped_fields = {
        "seeing": "seeing_observed_fwhm",
        "moon_proximity_deg": "moon_angular_separation_session",
        "sqm": "sky_sqm_observed",
        "guiding_rms": "guiding_rms_avg_arcsec",
        "camera_temp_c": "camera_temp_setpoint_c",
        "rig_snapshot_efl": "rig_efl_snapshot",
        "rig_snapshot_fratio": "rig_fr_snapshot",
        "rig_snapshot_image_scale": "rig_scale_snapshot",
        "rig_snapshot_fov_w": "rig_fov_w_snapshot",
        "rig_snapshot_fov_h": "rig_fov_h_snapshot",
        "calculated_integration_time_minutes": "calculated_integration_time_minutes",
    }
    for json_field, model_field in float_mapped_fields.items():
        if json_field in data:
            setattr(s, model_field, _to_float(data[json_field]))

    int_mapped_fields = {
        "moon_phase_pct": "moon_illumination_session",
        "gain": "gain_setting",
        "offset": "offset_setting",
        "rating": "session_rating_subjective",
        "l_subs": "filter_L_subs",
        "l_exposure": "filter_L_exposure_sec",
        "r_subs": "filter_R_subs",
        "r_exposure": "filter_R_exposure_sec",
        "g_subs": "filter_G_subs",
        "g_exposure": "filter_G_exposure_sec",
        "b_subs": "filter_B_subs",
        "b_exposure": "filter_B_exposure_sec",
        "ha_subs": "filter_Ha_subs",
        "ha_exposure": "filter_Ha_exposure_sec",
        "oiii_subs": "filter_OIII_subs",
        "oiii_exposure": "filter_OIII_exposure_sec",
        "sii_subs": "filter_SII_subs",
        "sii_exposure": "filter_SII_exposure_sec",
    }
    for json_field, model_field in int_mapped_fields.items():
        if json_field in data:
            setattr(s, model_field, _to_int(data[json_field]))

    if "project_id" in data:
        s.project_id = data["project_id"]
    if "custom_filter_data" in data:
        s.custom_filter_data = data["custom_filter_data"]


# ──────────────────────────────────────────────────────────
#  SAVED VIEWS
# ──────────────────────────────────────────────────────────


@rest_api_bp.route("/views", methods=["GET"])
@api_key_required
@api_permission_required("views.view")
def list_views():
    db = _db()
    try:
        q = db.query(SavedView).filter(SavedView.user_id == _user_id())
        q = q.order_by(SavedView.name)
        items, meta = _paginate(q)
        return _ok([_serialize_saved_view(v) for v in items], meta)
    finally:
        db.remove()


@rest_api_bp.route("/views", methods=["POST"])
@api_key_required
@api_permission_required("views.create")
def create_view():
    data = request.get_json(silent=True) or {}
    if not data.get("name"):
        return _err("name is required")
    db = _db()
    try:
        v = SavedView(user_id=_user_id())
        _apply_view_fields(v, data)
        db.add(v)
        db.commit()
        db.refresh(v)
        return _ok(_serialize_saved_view(v), status=201)
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


@rest_api_bp.route("/views/<int:view_id>", methods=["GET"])
@api_key_required
@api_permission_required("views.view")
def get_view(view_id):
    db = _db()
    try:
        v = (
            db.query(SavedView)
            .filter(
                SavedView.id == view_id,
                SavedView.user_id == _user_id(),
            )
            .first()
        )
        if v is None:
            return _err("View not found", 404)
        return _ok(_serialize_saved_view(v))
    finally:
        db.remove()


@rest_api_bp.route("/views/<int:view_id>", methods=["PUT"])
@api_key_required
@api_permission_required("views.edit")
def update_view(view_id):
    data = request.get_json(silent=True) or {}
    db = _db()
    try:
        v = (
            db.query(SavedView)
            .filter(
                SavedView.id == view_id,
                SavedView.user_id == _user_id(),
            )
            .first()
        )
        if v is None:
            return _err("View not found", 404)
        _apply_view_fields(v, data)
        db.commit()
        db.refresh(v)
        return _ok(_serialize_saved_view(v))
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


@rest_api_bp.route("/views/<int:view_id>", methods=["DELETE"])
@api_key_required
@api_permission_required("views.delete")
def delete_view(view_id):
    db = _db()
    try:
        v = (
            db.query(SavedView)
            .filter(
                SavedView.id == view_id,
                SavedView.user_id == _user_id(),
            )
            .first()
        )
        if v is None:
            return _err("View not found", 404)
        db.delete(v)
        db.commit()
        return _ok({"deleted": True})
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


def _apply_view_fields(v, data):
    for f in ["name", "description", "settings_json", "is_shared"]:
        if f in data:
            setattr(v, f, data[f])


# ──────────────────────────────────────────────────────────
#  SAVED FRAMINGS
# ──────────────────────────────────────────────────────────


@rest_api_bp.route("/framings", methods=["GET"])
@api_key_required
@api_permission_required("framings.view")
def list_framings():
    db = _db()
    try:
        q = db.query(SavedFraming).filter(SavedFraming.user_id == _user_id())
        q = q.order_by(SavedFraming.object_name)
        items, meta = _paginate(q)
        return _ok([_serialize_framing(f) for f in items], meta)
    finally:
        db.remove()


@rest_api_bp.route("/framings", methods=["POST"])
@api_key_required
@api_permission_required("framings.create")
def create_framing():
    data = request.get_json(silent=True) or {}
    if not data.get("object_name"):
        return _err("object_name is required")
    db = _db()
    try:
        f = SavedFraming(user_id=_user_id())
        _apply_framing_fields(f, data)
        db.add(f)
        db.commit()
        db.refresh(f)
        return _ok(_serialize_framing(f), status=201)
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


@rest_api_bp.route("/framings/<int:framing_id>", methods=["GET"])
@api_key_required
@api_permission_required("framings.view")
def get_framing(framing_id):
    db = _db()
    try:
        f = (
            db.query(SavedFraming)
            .filter(
                SavedFraming.id == framing_id,
                SavedFraming.user_id == _user_id(),
            )
            .first()
        )
        if f is None:
            return _err("Framing not found", 404)
        return _ok(_serialize_framing(f))
    finally:
        db.remove()


@rest_api_bp.route("/framings/<int:framing_id>", methods=["PUT"])
@api_key_required
@api_permission_required("framings.edit")
def update_framing(framing_id):
    data = request.get_json(silent=True) or {}
    db = _db()
    try:
        f = (
            db.query(SavedFraming)
            .filter(
                SavedFraming.id == framing_id,
                SavedFraming.user_id == _user_id(),
            )
            .first()
        )
        if f is None:
            return _err("Framing not found", 404)
        _apply_framing_fields(f, data)
        db.commit()
        db.refresh(f)
        return _ok(_serialize_framing(f))
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


@rest_api_bp.route("/framings/<int:framing_id>", methods=["DELETE"])
@api_key_required
@api_permission_required("framings.delete")
def delete_framing(framing_id):
    db = _db()
    try:
        f = (
            db.query(SavedFraming)
            .filter(
                SavedFraming.id == framing_id,
                SavedFraming.user_id == _user_id(),
            )
            .first()
        )
        if f is None:
            return _err("Framing not found", 404)
        db.delete(f)
        db.commit()
        return _ok({"deleted": True})
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


def _apply_framing_fields(f, data):
    if "object_name" in data:
        f.object_name = data["object_name"]
    if "rig_id" in data:
        f.rig_id = data["rig_id"]

    mapped_fields = {
        "survey_name": "survey",
        "survey_ra_hours": "ra",
        "survey_dec_deg": "dec",
        "survey_rotation_deg": "rotation",
        "mosaic_panels_x": "mosaic_cols",
        "mosaic_panels_y": "mosaic_rows",
        "mosaic_overlap_pct": "mosaic_overlap",
        "image_brightness": "img_brightness",
        "image_contrast": "img_contrast",
        "image_saturation": "img_saturation",
        "geo_belt_enabled": "geo_belt_enabled",
    }
    for json_field, model_field in mapped_fields.items():
        if json_field in data:
            setattr(f, model_field, data[json_field])
    f.updated_at = datetime.utcnow()


# ──────────────────────────────────────────────────────────
#  CUSTOM FILTERS
# ──────────────────────────────────────────────────────────


@rest_api_bp.route("/custom-filters", methods=["GET"])
@api_key_required
@api_permission_required("filters.view")
def list_custom_filters():
    db = _db()
    try:
        q = db.query(UserCustomFilter).filter(UserCustomFilter.user_id == _user_id())
        q = q.order_by(UserCustomFilter.filter_key)
        items, meta = _paginate(q)
        return _ok([_serialize_custom_filter(cf) for cf in items], meta)
    finally:
        db.remove()


@rest_api_bp.route("/custom-filters", methods=["POST"])
@api_key_required
@api_permission_required("filters.create")
def create_custom_filter():
    data = request.get_json(silent=True) or {}
    if not data.get("filter_key") or not data.get("filter_label"):
        return _err("filter_key and filter_label are required")
    db = _db()
    try:
        cf = UserCustomFilter(
            user_id=_user_id(),
            filter_key=data["filter_key"],
            filter_label=data["filter_label"],
        )
        db.add(cf)
        db.commit()
        db.refresh(cf)
        return _ok(_serialize_custom_filter(cf), status=201)
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


@rest_api_bp.route("/custom-filters/<int:filter_id>", methods=["DELETE"])
@api_key_required
@api_permission_required("filters.delete")
def delete_custom_filter(filter_id):
    db = _db()
    try:
        cf = (
            db.query(UserCustomFilter)
            .filter(
                UserCustomFilter.id == filter_id,
                UserCustomFilter.user_id == _user_id(),
            )
            .first()
        )
        if cf is None:
            return _err("Custom filter not found", 404)
        db.delete(cf)
        db.commit()
        return _ok({"deleted": True})
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


# ──────────────────────────────────────────────────────────
#  UI PREFERENCES
# ──────────────────────────────────────────────────────────


@rest_api_bp.route("/preferences", methods=["GET"])
@api_key_required
@api_permission_required("settings.view")
def get_preferences():
    db = _db()
    try:
        pref = db.query(UiPref).filter(UiPref.user_id == _user_id()).first()
        if pref is None:
            return _ok({"id": None, "json_blob": None})
        return _ok(_serialize_ui_pref(pref))
    finally:
        db.remove()


@rest_api_bp.route("/preferences", methods=["PUT"])
@api_key_required
@api_permission_required("settings.edit")
def update_preferences():
    data = request.get_json(silent=True) or {}
    if "json_blob" not in data:
        return _err("json_blob is required")
    db = _db()
    try:
        pref = db.query(UiPref).filter(UiPref.user_id == _user_id()).first()
        if pref is None:
            pref = UiPref(user_id=_user_id(), json_blob=data["json_blob"])
            db.add(pref)
        else:
            pref.json_blob = data["json_blob"]
        db.commit()
        db.refresh(pref)
        return _ok(_serialize_ui_pref(pref))
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


# ──────────────────────────────────────────────────────────
#  STELLARIUM SETTINGS  (per-user, stored in UiPref blob)
# ──────────────────────────────────────────────────────────


_STELLARIUM_DEFAULTS = {
    "host": "localhost",
    "port": 8090,
    "enabled": False,
}


def _get_stellarium_settings(db, user_id):
    """Extract stellarium settings from UiPref json_blob."""
    import json as _json

    pref = db.query(UiPref).filter(UiPref.user_id == user_id).first()
    if pref and pref.json_blob:
        try:
            blob = _json.loads(pref.json_blob)
            stel = blob.get("stellarium", {})
            return {
                "host": stel.get("host", _STELLARIUM_DEFAULTS["host"]),
                "port": int(stel.get("port", _STELLARIUM_DEFAULTS["port"])),
                "enabled": bool(stel.get("enabled", _STELLARIUM_DEFAULTS["enabled"])),
            }
        except (ValueError, TypeError):
            pass
    return dict(_STELLARIUM_DEFAULTS)


@rest_api_bp.route("/stellarium/settings", methods=["GET"])
@api_key_or_login_required
def get_stellarium_settings():
    db = _db()
    try:
        return _ok(_get_stellarium_settings(db, _user_id()))
    finally:
        db.remove()


@rest_api_bp.route("/stellarium/settings", methods=["PUT"])
@api_key_or_login_required
def update_stellarium_settings():
    import json as _json

    data = request.get_json(silent=True) or {}
    host = data.get("host", "").strip()
    port = data.get("port")
    enabled = data.get("enabled")

    if not host:
        return _err("host is required")
    try:
        port = int(port)
        if not (1 <= port <= 65535):
            raise ValueError
    except (TypeError, ValueError):
        return _err("port must be a valid number (1-65535)")

    db = _db()
    try:
        pref = db.query(UiPref).filter(UiPref.user_id == _user_id()).first()
        blob = {}
        if pref and pref.json_blob:
            try:
                blob = _json.loads(pref.json_blob)
            except (ValueError, TypeError):
                blob = {}

        blob["stellarium"] = {
            "host": host,
            "port": port,
            "enabled": bool(enabled),
        }

        new_blob = _json.dumps(blob)
        if pref is None:
            pref = UiPref(user_id=_user_id(), json_blob=new_blob)
            db.add(pref)
        else:
            pref.json_blob = new_blob
        db.commit()
        return _ok(blob["stellarium"])
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


# ──────────────────────────────────────────────────────────
#  AUTH  (register / login — multi-user mode only)
# ──────────────────────────────────────────────────────────


@rest_api_bp.route("/auth/register", methods=["POST"])
def register():
    """
    Register a new user account.

    Expects JSON: {"username": "...", "password": "..."}
    Returns the newly created API key.
    Disabled in single-user mode.
    """
    if SINGLE_USER_MODE:
        return _err("Registration is disabled in single-user mode", 403)

    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or not password:
        return _err("Both 'username' and 'password' are required", 400)
    if len(password) < 8:
        return _err("Password must be at least 8 characters", 400)

    db = _db()
    try:
        if db.query(DbUser).filter_by(username=username).first():
            return _err("Username already taken", 409)

        user = DbUser(
            username=username,
            password_hash=generate_password_hash(password),
            active=True,
        )
        db.add(user)
        db.flush()

        raw_key = create_api_key(db, user.id, name="default")
        db.commit()

        return _ok(
            {
                "user_id": user.id,
                "username": user.username,
                "api_key": raw_key,
            },
            status=201,
        )
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.close()


@rest_api_bp.route("/auth/login", methods=["POST"])
def login():
    """
    Authenticate with username + password and receive an API key.

    Expects JSON: {"username": "...", "password": "..."}
    Creates a new API key on each successful login.
    Disabled in single-user mode.
    """
    if SINGLE_USER_MODE:
        return _err("Login is disabled in single-user mode", 403)

    data = request.get_json(silent=True) or {}
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    if not username or not password:
        return _err("Both 'username' and 'password' are required", 400)

    db = _db()
    try:
        user = db.query(DbUser).filter_by(username=username).first()
        if not user or not user.password_hash:
            return _err("Invalid username or password", 401)
        if not check_password_hash(user.password_hash, password):
            return _err("Invalid username or password", 401)
        if not user.active:
            return _err("Account is deactivated", 403)

        raw_key = create_api_key(db, user.id, name="login")
        db.commit()

        return _ok(
            {
                "user_id": user.id,
                "username": user.username,
                "api_key": raw_key,
            }
        )
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.close()


# ──────────────────────────────────────────────────────────
#  API KEYS  (self-service management)
# ──────────────────────────────────────────────────────────


@rest_api_bp.route("/api-keys", methods=["GET"])
@api_key_required
@api_permission_required("api_keys.view")
def list_api_keys():
    """List all API keys belonging to the authenticated user."""
    db = _db()
    try:
        keys = (
            db.query(ApiKey)
            .filter(ApiKey.user_id == _user_id())
            .order_by(ApiKey.created_at.desc())
            .all()
        )
        return _ok([_serialize_api_key(k) for k in keys])
    finally:
        db.remove()


@rest_api_bp.route("/api-keys", methods=["POST"])
@api_key_required
@api_permission_required("api_keys.manage")
def create_api_key_endpoint():
    """Create a new API key.  Returns the raw key (only time it's visible)."""
    data = request.get_json(silent=True) or {}
    name = data.get("name", "unnamed")
    db = _db()
    try:
        # Limit keys per user to prevent abuse
        count = (
            db.query(ApiKey)
            .filter(
                ApiKey.user_id == _user_id(),
                ApiKey.is_active.is_(True),
            )
            .count()
        )
        if count >= 25:
            return _err("Maximum 25 active API keys per user", 429)

        raw_key = create_api_key(db, _user_id(), name=name)
        # The db session in create_api_key already committed; re-query to serialize
        new_key = (
            db.query(ApiKey)
            .filter(
                ApiKey.key_hash == hash_api_key(raw_key),
            )
            .first()
        )
        result = _serialize_api_key(new_key)
        result["key"] = raw_key  # Only time the full key is revealed
        return _ok(result, status=201)
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


@rest_api_bp.route("/api-keys/<int:key_id>", methods=["DELETE"])
@api_key_required
@api_permission_required("api_keys.manage")
def revoke_api_key(key_id):
    """Revoke (deactivate) an API key.  Cannot delete the key you're using."""
    db = _db()
    try:
        api_key = (
            db.query(ApiKey)
            .filter(
                ApiKey.id == key_id,
                ApiKey.user_id == _user_id(),
            )
            .first()
        )
        if api_key is None:
            return _err("API key not found", 404)

        # Prevent revoking the key currently in use
        if hasattr(g, "api_key_obj") and g.api_key_obj.id == key_id:
            return _err("Cannot revoke the API key you are currently using", 400)

        api_key.is_active = False
        db.commit()
        return _ok({"revoked": True})
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


# ──────────────────────────────────────────────────────────
#  ADMIN: USER MANAGEMENT  (multi-user mode only)
# ──────────────────────────────────────────────────────────


@rest_api_bp.route("/admin/users", methods=["GET"])
@api_key_required
@api_admin_required
def admin_list_users():
    """List all users (admin only, multi-user mode)."""
    if SINGLE_USER_MODE:
        return _err("Not available in single-user mode", 403)
    db = _db()
    try:
        users = (
            db.query(DbUser)
            .options(selectinload(DbUser.roles))
            .order_by(DbUser.username)
            .all()
        )
        return _ok(
            [
                {
                    "id": u.id,
                    "username": u.username,
                    "active": u.active,
                    "roles": [r.name for r in u.roles],
                }
                for u in users
            ]
        )
    finally:
        db.remove()


# ──────────────────────────────────────────────────────────
#  STATUS / INFO
# ──────────────────────────────────────────────────────────


@rest_api_bp.route("/status", methods=["GET"])
@api_key_required
def api_status():
    """Return server info and authenticated user context."""
    from nova.config import APP_VERSION

    return _ok(
        {
            "version": APP_VERSION,
            "single_user_mode": SINGLE_USER_MODE,
            "user": g.db_user.username,
        }
    )


# ──────────────────────────────────────────────────────────
#  SHARING: Read Endpoints
# ──────────────────────────────────────────────────────────


def _serialize_shared_object(obj, owner_username=None):
    """Serialize a shared object with owner info."""
    data = _serialize_object(obj)
    data["owner_username"] = owner_username or "unknown"
    data["user_id"] = obj.user_id
    return data


def _serialize_shared_component(comp, owner_username=None):
    """Serialize a shared component with owner info."""
    data = _serialize_component(comp)
    data["owner_username"] = owner_username or "unknown"
    data["user_id"] = comp.user_id
    return data


def _serialize_shared_view(view, owner_username=None):
    """Serialize a shared view with owner info."""
    data = _serialize_saved_view(view)
    data["owner_username"] = owner_username or "unknown"
    data["user_id"] = view.user_id
    return data


@rest_api_bp.route("/shared/objects", methods=["GET"])
@api_key_or_login_required
@api_permission_required("shared.objects.view")
def get_shared_objects():
    """Return all shared objects from all users."""
    db = _db()
    try:
        # Get all shared objects with their owners
        shared_objects = (
            db.query(AstroObject, DbUser.username)
            .join(DbUser, AstroObject.user_id == DbUser.id)
            .filter(AstroObject.is_shared == True)
            .order_by(AstroObject.object_name)
            .all()
        )
        return _ok(
            {
                "objects": [
                    _serialize_shared_object(obj, username)
                    for obj, username in shared_objects
                ]
            }
        )
    finally:
        db.remove()


@rest_api_bp.route("/shared/components", methods=["GET"])
@api_key_or_login_required
@api_permission_required("shared.components.view")
def get_shared_components():
    """Return all shared components from all users."""
    db = _db()
    try:
        shared_components = (
            db.query(Component, DbUser.username)
            .join(DbUser, Component.user_id == DbUser.id)
            .filter(Component.is_shared == True)
            .order_by(Component.name)
            .all()
        )
        return _ok(
            {
                "components": [
                    _serialize_shared_component(comp, username)
                    for comp, username in shared_components
                ]
            }
        )
    finally:
        db.remove()


@rest_api_bp.route("/shared/views", methods=["GET"])
@api_key_or_login_required
@api_permission_required("shared.views.view")
def get_shared_views():
    """Return all shared saved views from all users."""
    db = _db()
    try:
        shared_views = (
            db.query(SavedView, DbUser.username)
            .join(DbUser, SavedView.user_id == DbUser.id)
            .filter(SavedView.is_shared == True)
            .order_by(SavedView.name)
            .all()
        )
        return _ok(
            {
                "views": [
                    _serialize_shared_view(view, username)
                    for view, username in shared_views
                ]
            }
        )
    finally:
        db.remove()


# ──────────────────────────────────────────────────────────
#  SHARING: Fork Endpoints
# ──────────────────────────────────────────────────────────


@rest_api_bp.route("/shared/objects/<int:object_id>/fork", methods=["POST"])
@api_key_or_login_required
@api_permission_required("shared.objects.fork")
def fork_shared_object(object_id):
    """Fork a shared object into the current user's collection."""
    db = _db()
    try:
        # Find the shared object
        source = db.query(AstroObject).filter_by(id=object_id).first()
        if not source:
            return _err("Object not found", 404)
        if not source.is_shared:
            return _err("Object is not shared", 403)

        user_id = _user_id()

        # Check for existing object with same name
        existing = (
            db.query(AstroObject)
            .filter_by(user_id=user_id, object_name=source.object_name)
            .first()
        )
        new_name = source.object_name
        if existing:
            # Append _copy suffix to avoid collision
            suffix = "_copy"
            counter = 1
            while True:
                new_name = f"{source.object_name}{suffix}"
                if counter > 1:
                    new_name = f"{source.object_name}{suffix}{counter}"
                check = (
                    db.query(AstroObject)
                    .filter_by(user_id=user_id, object_name=new_name)
                    .first()
                )
                if not check:
                    break
                counter += 1

        # Create the fork
        forked = AstroObject(
            user_id=user_id,
            object_name=new_name,
            common_name=source.common_name,
            ra_hours=source.ra_hours,
            dec_deg=source.dec_deg,
            type=source.type,
            constellation=source.constellation,
            magnitude=source.magnitude,
            size=source.size,
            sb=source.sb,
            active_project=False,
            project_name=None,
            is_shared=False,  # Fork is private by default
            shared_notes=None,
            original_user_id=source.user_id,
            original_item_id=source.id,
            catalog_sources=source.catalog_sources,
            catalog_info=source.catalog_info,
            enabled=source.enabled,
            image_url=source.image_url,
            image_credit=source.image_credit,
            image_source_link=source.image_source_link,
            description_text=source.description_text,
            description_credit=source.description_credit,
            description_source_link=source.description_source_link,
        )
        db.add(forked)
        db.commit()
        db.refresh(forked)
        return _ok(_serialize_object(forked), status=201)
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


@rest_api_bp.route("/shared/components/<int:component_id>/fork", methods=["POST"])
@api_key_or_login_required
@api_permission_required("shared.objects.fork")
def fork_shared_component(component_id):
    """Fork a shared component into the current user's equipment."""
    db = _db()
    try:
        source = db.query(Component).filter_by(id=component_id).first()
        if not source:
            return _err("Component not found", 404)
        if not source.is_shared:
            return _err("Component is not shared", 403)

        user_id = _user_id()

        # Check for existing component with same name
        existing = (
            db.query(Component).filter_by(user_id=user_id, name=source.name).first()
        )
        new_name = source.name
        if existing:
            suffix = "_copy"
            counter = 1
            while True:
                new_name = f"{source.name}{suffix}"
                if counter > 1:
                    new_name = f"{source.name}{suffix}{counter}"
                check = (
                    db.query(Component)
                    .filter_by(user_id=user_id, name=new_name)
                    .first()
                )
                if not check:
                    break
                counter += 1

        # Create the fork (generate new stable_uid)
        import uuid as uuid_mod

        forked = Component(
            stable_uid=str(uuid_mod.uuid4()),
            user_id=user_id,
            kind=source.kind,
            name=new_name,
            aperture_mm=source.aperture_mm,
            focal_length_mm=source.focal_length_mm,
            sensor_width_mm=source.sensor_width_mm,
            sensor_height_mm=source.sensor_height_mm,
            pixel_size_um=source.pixel_size_um,
            factor=source.factor,
            is_shared=False,
            original_user_id=source.user_id,
            original_item_id=source.id,
        )
        db.add(forked)
        db.commit()
        db.refresh(forked)
        return _ok(_serialize_component(forked), status=201)
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


@rest_api_bp.route("/shared/views/<int:view_id>/fork", methods=["POST"])
@api_key_or_login_required
@api_permission_required("shared.objects.fork")
def fork_shared_view(view_id):
    """Fork a shared view into the current user's saved views."""
    db = _db()
    try:
        source = db.query(SavedView).filter_by(id=view_id).first()
        if not source:
            return _err("View not found", 404)
        if not source.is_shared:
            return _err("View is not shared", 403)

        user_id = _user_id()

        # Check for existing view with same name
        existing = (
            db.query(SavedView).filter_by(user_id=user_id, name=source.name).first()
        )
        new_name = source.name
        if existing:
            suffix = "_copy"
            counter = 1
            while True:
                new_name = f"{source.name}{suffix}"
                if counter > 1:
                    new_name = f"{source.name}{suffix}{counter}"
                check = (
                    db.query(SavedView)
                    .filter_by(user_id=user_id, name=new_name)
                    .first()
                )
                if not check:
                    break
                counter += 1

        forked = SavedView(
            user_id=user_id,
            name=new_name,
            description=source.description,
            settings_json=source.settings_json,
            is_shared=False,
            original_user_id=source.user_id,
            original_item_id=source.id,
        )
        db.add(forked)
        db.commit()
        db.refresh(forked)
        return _ok(_serialize_saved_view(forked), status=201)
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


# ──────────────────────────────────────────────────────────
#  ROLE / PERMISSION CRUD API (Admin only)
# ──────────────────────────────────────────────────────────


def _serialize_permission(p):
    """Serialize a Permission object."""
    return {"id": p.id, "name": p.name, "description": p.description}


def _serialize_role(r):
    """Serialize a Role object with its permissions."""
    return {
        "id": r.id,
        "name": r.name,
        "description": r.description,
        "is_system": r.is_system,
        "permissions": [_serialize_permission(p) for p in r.permissions],
    }


# ── Permissions (read-only for now) ─────────────────────────


@rest_api_bp.route("/admin/permissions", methods=["GET"])
@api_key_or_login_required
@api_admin_required
def admin_list_permissions():
    """List all permissions in the system."""
    db = _db()
    try:
        perms = db.query(Permission).order_by(Permission.name).all()
        return _ok({"permissions": [_serialize_permission(p) for p in perms]})
    finally:
        db.remove()


# ── Roles CRUD ──────────────────────────────────────────────


@rest_api_bp.route("/admin/roles", methods=["GET"])
@api_key_or_login_required
@api_admin_required
def admin_list_roles():
    """List all roles in the system."""
    db = _db()
    try:
        roles = db.query(Role).order_by(Role.name).all()
        return _ok({"roles": [_serialize_role(r) for r in roles]})
    finally:
        db.remove()


@rest_api_bp.route("/admin/roles", methods=["POST"])
@api_key_or_login_required
@api_admin_required
def admin_create_role():
    """Create a new role."""
    db = _db()
    try:
        data = request.get_json() or {}
        name = data.get("name", "").strip()
        if not name:
            return _err("Role name is required", 400)

        # Check for duplicate name
        existing = db.query(Role).filter_by(name=name).first()
        if existing:
            return _err(f"Role '{name}' already exists", 409)

        role = Role(
            name=name,
            description=data.get("description", ""),
            is_system=False,  # User-created roles are never system roles
        )
        db.add(role)
        db.commit()
        db.refresh(role)
        return _ok(_serialize_role(role), status=201)
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


@rest_api_bp.route("/admin/roles/<int:role_id>", methods=["GET"])
@api_key_or_login_required
@api_admin_required
def admin_get_role(role_id):
    """Get a specific role by ID."""
    db = _db()
    try:
        role = db.query(Role).filter_by(id=role_id).first()
        if not role:
            return _err("Role not found", 404)
        return _ok(_serialize_role(role))
    finally:
        db.remove()


@rest_api_bp.route("/admin/roles/<int:role_id>", methods=["PUT"])
@api_key_or_login_required
@api_admin_required
def admin_update_role(role_id):
    """Update a role's name or description."""
    db = _db()
    try:
        role = db.query(Role).filter_by(id=role_id).first()
        if not role:
            return _err("Role not found", 404)

        data = request.get_json() or {}

        if "name" in data:
            new_name = data["name"].strip()
            if not new_name:
                return _err("Role name cannot be empty", 400)
            # Check for duplicate name (excluding self)
            dup = (
                db.query(Role).filter(Role.name == new_name, Role.id != role_id).first()
            )
            if dup:
                return _err(f"Role '{new_name}' already exists", 409)
            role.name = new_name

        if "description" in data:
            role.description = data["description"]

        db.commit()
        db.refresh(role)
        return _ok(_serialize_role(role))
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


@rest_api_bp.route("/admin/roles/<int:role_id>", methods=["DELETE"])
@api_key_or_login_required
@api_admin_required
def admin_delete_role(role_id):
    """Delete a role. System roles cannot be deleted."""
    db = _db()
    try:
        role = db.query(Role).filter_by(id=role_id).first()
        if not role:
            return _err("Role not found", 404)

        if role.is_system:
            return _err(f"Cannot delete system role '{role.name}'", 403)

        db.delete(role)
        db.commit()
        return _ok({"message": f"Role '{role.name}' deleted"})
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


# ── Role-Permission Assignment ──────────────────────────────


@rest_api_bp.route(
    "/admin/roles/<int:role_id>/permissions/<int:perm_id>", methods=["POST"]
)
@api_key_or_login_required
@api_admin_required
def admin_assign_permission_to_role(role_id, perm_id):
    """Assign a permission to a role."""
    db = _db()
    try:
        role = db.query(Role).filter_by(id=role_id).first()
        if not role:
            return _err("Role not found", 404)

        perm = db.query(Permission).filter_by(id=perm_id).first()
        if not perm:
            return _err("Permission not found", 404)

        if perm in role.permissions:
            return _err(f"Role already has permission '{perm.name}'", 409)

        role.permissions.append(perm)
        db.commit()
        db.refresh(role)
        return _ok(_serialize_role(role))
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


@rest_api_bp.route(
    "/admin/roles/<int:role_id>/permissions/<int:perm_id>", methods=["DELETE"]
)
@api_key_or_login_required
@api_admin_required
def admin_revoke_permission_from_role(role_id, perm_id):
    """Revoke a permission from a role."""
    db = _db()
    try:
        role = db.query(Role).filter_by(id=role_id).first()
        if not role:
            return _err("Role not found", 404)

        perm = db.query(Permission).filter_by(id=perm_id).first()
        if not perm:
            return _err("Permission not found", 404)

        if perm not in role.permissions:
            return _err(f"Role does not have permission '{perm.name}'", 404)

        role.permissions.remove(perm)
        db.commit()
        db.refresh(role)
        return _ok(_serialize_role(role))
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


# ── User-Role Assignment ────────────────────────────────────


@rest_api_bp.route("/admin/users/<int:user_id>/roles", methods=["GET"])
@api_key_or_login_required
@api_admin_required
def admin_get_user_roles(user_id):
    """Get all roles assigned to a user."""
    db = _db()
    try:
        user = db.query(DbUser).filter_by(id=user_id).first()
        if not user:
            return _err("User not found", 404)
        return _ok(
            {
                "user_id": user.id,
                "username": user.username,
                "roles": [_serialize_role(r) for r in user.roles],
            }
        )
    finally:
        db.remove()


@rest_api_bp.route("/admin/users/<int:user_id>/roles/<int:role_id>", methods=["POST"])
@api_key_or_login_required
@api_admin_required
def admin_assign_role_to_user(user_id, role_id):
    """Assign a role to a user."""
    db = _db()
    try:
        user = db.query(DbUser).filter_by(id=user_id).first()
        if not user:
            return _err("User not found", 404)

        role = db.query(Role).filter_by(id=role_id).first()
        if not role:
            return _err("Role not found", 404)

        if role in user.roles:
            return _err(f"User already has role '{role.name}'", 409)

        user.roles.append(role)
        db.commit()
        db.refresh(user)
        return _ok(
            {
                "user_id": user.id,
                "username": user.username,
                "roles": [_serialize_role(r) for r in user.roles],
            }
        )
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


@rest_api_bp.route("/admin/users/<int:user_id>/roles/<int:role_id>", methods=["DELETE"])
@api_key_or_login_required
@api_admin_required
def admin_revoke_role_from_user(user_id, role_id):
    """Revoke a role from a user."""
    db = _db()
    try:
        user = db.query(DbUser).filter_by(id=user_id).first()
        if not user:
            return _err("User not found", 404)

        role = db.query(Role).filter_by(id=role_id).first()
        if not role:
            return _err("Role not found", 404)

        if role not in user.roles:
            return _err(f"User does not have role '{role.name}'", 404)

        user.roles.remove(role)
        db.commit()
        db.refresh(user)
        return _ok(
            {
                "user_id": user.id,
                "username": user.username,
                "roles": [_serialize_role(r) for r in user.roles],
            }
        )
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


# ──────────────────────────────────────────────────────────
#  BLOG POSTS  (BlogPost CRUD)
# ──────────────────────────────────────────────────────────


@rest_api_bp.route("/blog/posts", methods=["GET"])
@api_key_required
@api_permission_required("blog.view")
def api_list_blog_posts():
    """List all blog posts with pagination and optional search."""
    db = _db()
    try:
        q = (
            db.query(BlogPost)
            .options(
                selectinload(BlogPost.images),
                selectinload(BlogPost.user),
            )
            .order_by(BlogPost.created_at.desc())
        )

        # Optional search filter (title ilike)
        search = request.args.get("search")
        if search:
            pattern = f"%{search}%"
            q = q.filter(BlogPost.title.ilike(pattern))

        items, meta = _paginate(q)
        return _ok([_serialize_blog_post(p, include_full=False) for p in items], meta)
    finally:
        db.remove()


@rest_api_bp.route("/blog/posts/<int:post_id>", methods=["GET"])
@api_key_required
@api_permission_required("blog.view")
def api_get_blog_post(post_id):
    """Get a single blog post with images and comments."""
    db = _db()
    try:
        post = (
            db.query(BlogPost)
            .options(
                selectinload(BlogPost.images),
                selectinload(BlogPost.comments).selectinload(BlogComment.user),
                selectinload(BlogPost.user),
            )
            .filter(BlogPost.id == post_id)
            .first()
        )
        if not post:
            return _err("Blog post not found", 404)

        return _ok(_serialize_blog_post(post, include_full=True))
    finally:
        db.remove()


@rest_api_bp.route("/blog/posts", methods=["POST"])
@api_key_required
@api_permission_required("blog.create")
def api_create_blog_post():
    """
    Create a new blog post.

    Supports both JSON and multipart/form-data:
    - JSON: {"title": "...", "content": "..."}
    - Multipart: title, content in form fields; images[] as file uploads
    """
    db = _db()
    try:
        # Detect content type
        is_multipart = (
            request.content_type and "multipart/form-data" in request.content_type
        )

        if is_multipart:
            title = (request.form.get("title") or "").strip()
            content = request.form.get("content") or ""
            captions = request.form.getlist("captions[]")
            files = request.files.getlist("images[]")
        else:
            data = request.get_json(silent=True) or {}
            title = (data.get("title") or "").strip()
            content = data.get("content") or ""
            captions = []
            files = []

        # Validation
        if not title:
            return _err("title is required", 400)
        if len(title) > 256:
            return _err("title must be 256 characters or fewer", 400)

        # Create post
        post = BlogPost(
            user_id=_user_id(),
            title=title,
            content=content,
        )
        db.add(post)
        db.flush()  # get post.id for image association

        # Handle image uploads (multipart only)
        for i, file in enumerate(files):
            caption = captions[i] if i < len(captions) else ""
            img = _save_blog_image(file, _user_id(), post.id, i, caption)
            if img:
                db.add(img)

        db.commit()
        db.refresh(post)

        # Re-query with eager loading for response
        post = (
            db.query(BlogPost)
            .options(selectinload(BlogPost.images), selectinload(BlogPost.user))
            .filter(BlogPost.id == post.id)
            .first()
        )

        return _ok(_serialize_blog_post(post, include_full=False), status=201)
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


@rest_api_bp.route("/blog/posts/<int:post_id>", methods=["PUT"])
@api_key_required
@api_permission_required("blog.edit")
def api_update_blog_post(post_id):
    """Update an existing blog post (owner or admin only)."""
    db = _db()
    try:
        post = db.query(BlogPost).filter(BlogPost.id == post_id).first()
        if not post:
            return _err("Blog post not found", 404)

        # Ownership check
        if (
            not SINGLE_USER_MODE
            and post.user_id != _user_id()
            and not g.db_user.is_admin
        ):
            return _err("Forbidden", 403)

        data = request.get_json(silent=True) or {}

        # Validate title length if provided
        if "title" in data:
            title = str(data["title"])
            if len(title) > 256:
                return _err("title must be 256 characters or fewer", 400)

        _apply_blog_post_fields(post, data)
        post.updated_at = datetime.utcnow()

        db.commit()
        db.refresh(post)

        # Re-query with eager loading
        post = (
            db.query(BlogPost)
            .options(selectinload(BlogPost.images), selectinload(BlogPost.user))
            .filter(BlogPost.id == post.id)
            .first()
        )

        return _ok(_serialize_blog_post(post, include_full=False))
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


@rest_api_bp.route("/blog/posts/<int:post_id>", methods=["DELETE"])
@api_key_required
@api_permission_required("blog.delete")
def api_delete_blog_post(post_id):
    """Delete a blog post (owner or admin only). Also deletes image files from disk."""
    db = _db()
    try:
        post = (
            db.query(BlogPost)
            .options(selectinload(BlogPost.images))
            .filter(BlogPost.id == post_id)
            .first()
        )
        if not post:
            return _err("Blog post not found", 404)

        # Ownership check
        if (
            not SINGLE_USER_MODE
            and post.user_id != _user_id()
            and not g.db_user.is_admin
        ):
            return _err("Forbidden", 403)

        # Delete image files from disk before removing from DB
        for img in post.images:
            _delete_blog_image_files(img, post.user_id)

        db.delete(post)  # cascade handles BlogImage and BlogComment rows
        db.commit()

        return _ok({"deleted": True})
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


# ──────────────────────────────────────────────────────────
#  BLOG IMAGES  (Image management for posts)
# ──────────────────────────────────────────────────────────


@rest_api_bp.route("/blog/posts/<int:post_id>/images", methods=["POST"])
@api_key_required
@api_permission_required("blog.edit")
def api_add_blog_images(post_id):
    """Add images to an existing blog post (owner or admin only)."""
    db = _db()
    try:
        post = (
            db.query(BlogPost)
            .options(selectinload(BlogPost.images))
            .filter(BlogPost.id == post_id)
            .first()
        )
        if not post:
            return _err("Blog post not found", 404)

        # Ownership check
        if (
            not SINGLE_USER_MODE
            and post.user_id != _user_id()
            and not g.db_user.is_admin
        ):
            return _err("Forbidden", 403)

        files = request.files.getlist("images[]")
        captions = request.form.getlist("captions[]")

        if not files:
            return _err("No images provided", 400)

        # Calculate starting display_order
        max_order = max((img.display_order for img in post.images), default=-1)

        new_images = []
        for i, file in enumerate(files):
            order = max_order + 1 + i
            caption = captions[i] if i < len(captions) else ""
            img = _save_blog_image(file, post.user_id, post.id, order, caption)
            if img:
                db.add(img)
                new_images.append(img)

        if not new_images:
            return _err("No valid images could be saved", 400)

        db.commit()
        for img in new_images:
            db.refresh(img)

        return _ok(
            [_serialize_blog_image(img, post.user_id) for img in new_images],
            status=201,
        )
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


@rest_api_bp.route(
    "/blog/posts/<int:post_id>/images/<int:image_id>", methods=["DELETE"]
)
@api_key_required
@api_permission_required("blog.edit")
def api_delete_blog_image(post_id, image_id):
    """Delete a single image from a blog post (owner or admin only)."""
    db = _db()
    try:
        post = db.query(BlogPost).filter(BlogPost.id == post_id).first()
        if not post:
            return _err("Blog post not found", 404)

        # Ownership check
        if (
            not SINGLE_USER_MODE
            and post.user_id != _user_id()
            and not g.db_user.is_admin
        ):
            return _err("Forbidden", 403)

        image = db.query(BlogImage).filter(BlogImage.id == image_id).first()
        if not image:
            return _err("Image not found", 404)

        # Verify image belongs to this post
        if image.post_id != post_id:
            return _err("Image does not belong to this post", 400)

        # Delete files from disk
        _delete_blog_image_files(image, post.user_id)

        db.delete(image)
        db.commit()

        return _ok({"deleted": True})
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


@rest_api_bp.route("/blog/posts/<int:post_id>/images/reorder", methods=["PUT"])
@api_key_required
@api_permission_required("blog.edit")
def api_reorder_blog_images(post_id):
    """
    Reorder images for a blog post.

    Body: [{"id": 3, "display_order": 0}, {"id": 7, "display_order": 1}, ...]
    """
    db = _db()
    try:
        post = (
            db.query(BlogPost)
            .options(selectinload(BlogPost.images))
            .filter(BlogPost.id == post_id)
            .first()
        )
        if not post:
            return _err("Blog post not found", 404)

        # Ownership check
        if (
            not SINGLE_USER_MODE
            and post.user_id != _user_id()
            and not g.db_user.is_admin
        ):
            return _err("Forbidden", 403)

        data = request.get_json(silent=True)
        if not data or not isinstance(data, list):
            return _err("Body must be a list of {id, display_order} objects", 400)

        # Build order map and validate
        order_map = {}
        post_image_ids = {img.id for img in post.images}

        for item in data:
            img_id = item.get("id")
            display_order = item.get("display_order")

            if img_id is None or display_order is None:
                return _err("Each item must have 'id' and 'display_order'", 400)

            if img_id not in post_image_ids:
                return _err(f"Image ID {img_id} does not belong to this post", 400)

            order_map[img_id] = display_order

        # Apply new order
        for img in post.images:
            if img.id in order_map:
                img.display_order = order_map[img.id]

        db.commit()

        # Re-fetch with updated order
        post = (
            db.query(BlogPost)
            .options(selectinload(BlogPost.images))
            .filter(BlogPost.id == post_id)
            .first()
        )

        return _ok([_serialize_blog_image(img, post.user_id) for img in post.images])
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


# ──────────────────────────────────────────────────────────
#  BLOG COMMENTS  (Comment CRUD for posts)
# ──────────────────────────────────────────────────────────


@rest_api_bp.route("/blog/posts/<int:post_id>/comments", methods=["GET"])
@api_key_required
@api_permission_required("blog.view")
def api_list_blog_comments(post_id):
    """List comments for a blog post with pagination."""
    db = _db()
    try:
        post = db.query(BlogPost).filter(BlogPost.id == post_id).first()
        if not post:
            return _err("Blog post not found", 404)

        q = (
            db.query(BlogComment)
            .options(selectinload(BlogComment.user))
            .filter(BlogComment.post_id == post_id)
            .order_by(BlogComment.created_at.asc())
        )

        items, meta = _paginate(q)
        return _ok([_serialize_blog_comment(c) for c in items], meta)
    finally:
        db.remove()


@rest_api_bp.route("/blog/posts/<int:post_id>/comments", methods=["POST"])
@api_key_required
@api_permission_required("blog.comment")
def api_create_blog_comment(post_id):
    """Add a comment to a blog post."""
    db = _db()
    try:
        post = db.query(BlogPost).filter(BlogPost.id == post_id).first()
        if not post:
            return _err("Blog post not found", 404)

        data = request.get_json(silent=True) or {}
        content = (data.get("content") or "").strip()

        if not content:
            return _err("content is required", 400)
        if len(content) > BLOG_COMMENT_MAX_LEN:
            return _err(
                f"content must be {BLOG_COMMENT_MAX_LEN} characters or fewer", 400
            )

        comment = BlogComment(
            post_id=post_id,
            user_id=_user_id(),
            content=content,
        )
        db.add(comment)
        db.commit()
        db.refresh(comment)

        # Re-query with user for username
        comment = (
            db.query(BlogComment)
            .options(selectinload(BlogComment.user))
            .filter(BlogComment.id == comment.id)
            .first()
        )

        return _ok(_serialize_blog_comment(comment), status=201)
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()


@rest_api_bp.route(
    "/blog/posts/<int:post_id>/comments/<int:comment_id>", methods=["DELETE"]
)
@api_key_required
@api_permission_required("blog.comment")
def api_delete_blog_comment(post_id, comment_id):
    """
    Delete a comment from a blog post.

    Allowed if:
    - Comment author (user_id matches)
    - Post owner (post.user_id matches)
    - Admin
    """
    db = _db()
    try:
        post = db.query(BlogPost).filter(BlogPost.id == post_id).first()
        if not post:
            return _err("Blog post not found", 404)

        comment = db.query(BlogComment).filter(BlogComment.id == comment_id).first()
        if not comment:
            return _err("Comment not found", 404)

        # Verify comment belongs to this post
        if comment.post_id != post_id:
            return _err("Comment does not belong to this post", 400)

        # Three-way ownership check
        is_comment_author = comment.user_id == _user_id()
        is_post_owner = post.user_id == _user_id()
        is_admin = g.db_user.is_admin if hasattr(g.db_user, "is_admin") else False

        if not SINGLE_USER_MODE and not (
            is_comment_author or is_post_owner or is_admin
        ):
            return _err("Forbidden", 403)

        db.delete(comment)
        db.commit()

        return _ok({"deleted": True})
    except Exception as e:
        db.rollback()
        return _err(str(e), 500)
    finally:
        db.remove()
