import io
import json
import os
import re
import uuid
import zipfile
import threading
import traceback

import yaml
from flask import (
    Blueprint, request, jsonify, redirect, url_for,
    render_template, flash, send_file, current_app, g
)
from flask_login import login_required, current_user
from flask_babel import gettext as _
from math import atan, degrees
from datetime import datetime, UTC
from sqlalchemy import func
from sqlalchemy.orm import selectinload

from nova.config import (
    SINGLE_USER_MODE, UPLOAD_FOLDER, INSTANCE_PATH,
    CONFIG_DIR, APP_VERSION, CACHE_DIR,
    DEFAULT_DITHER_MAIN_SHIFT_PX,
)
from nova.helpers import (
    get_db, allowed_file, get_user_log_string,
    calculate_dither_recommendation,
    safe_int, _compute_rig_metrics_from_components,
    dither_display, sort_rigs,
)
from nova.models import (
    DbUser, AstroObject, Component, Rig, Location,
    JournalSession, Project, UserCustomFilter,
    SavedFraming, SavedView, UiPref,
)
from nova.migration import (
    _upsert_user,
    validate_journal_data, repair_journals,
    load_catalog_pack, import_catalog_pack_for_user,
    export_user_to_yaml, import_user_from_yaml,
    _migrate_components_and_rigs, _migrate_journal,
    _migrate_locations, _migrate_objects,
    _migrate_ui_prefs, _migrate_saved_views,
    _migrate_saved_framings,
)
from modules.config_validation import validate_config

tools_bp = Blueprint('tools', __name__)

@tools_bp.route('/add_component', methods=['POST'])
@login_required
def add_component():
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        user = db.query(DbUser).filter_by(username=username).one()
        form = request.form
        form_kind = form.get('component_type')
        kind_map = {
            'telescopes': 'telescope',
            'cameras': 'camera',
            'reducers_extenders': 'reducer_extender'
        }
        kind = kind_map.get(form_kind)

        if not kind:
            db.rollback()
            flash(_("Error: Unknown component type '%(component_type)s'.", component_type=form_kind), "error")
            return redirect(url_for('core.config_form'))

        new_comp = Component(user_id=user.id, kind=kind, name=form.get('name'))

        # --- ADD THIS LINE ---
        new_comp.is_shared = request.form.get("is_shared") == "on"
        # --- END OF ADDITION ---

        if kind == 'telescope':
            new_comp.aperture_mm = float(form.get('aperture_mm'))
            new_comp.focal_length_mm = float(form.get('focal_length_mm'))
        elif kind == 'camera':
            new_comp.sensor_width_mm = float(form.get('sensor_width_mm'))
            new_comp.sensor_height_mm = float(form.get('sensor_height_mm'))
            new_comp.pixel_size_um = float(form.get('pixel_size_um'))
        elif kind == 'reducer_extender':
            new_comp.factor = float(form.get('factor'))

        db.add(new_comp)
        db.commit()
        flash(_("Component '%(component_name)s' added successfully.", component_name=new_comp.name), "success")
    except Exception as e:
        db.rollback()
        flash(_("Error adding component: %(error)s", error=e), "error")
    return redirect(url_for('core.config_form'))


@tools_bp.route('/update_component', methods=['POST'])
@login_required
def update_component():
    db = get_db()
    try:
        form = request.form
        comp_id = int(form.get('component_id'))
        comp = db.get(Component, comp_id)

        # Security check: ensure component belongs to the current user
        if comp.user.username != ("default" if SINGLE_USER_MODE else current_user.username):
            flash(_("Authorization error."), "error")
            return redirect(url_for('core.config_form'))

        comp.name = form.get('name')

        # --- ADD THIS LOGIC ---
        # Only allow updating 'is_shared' if it's not an imported item
        if not comp.original_user_id:
            comp.is_shared = request.form.get("is_shared") == "on"
        # --- END OF ADDITION ---

        if comp.kind == 'telescope':
            comp.aperture_mm = float(form.get('aperture_mm'))
            comp.focal_length_mm = float(form.get('focal_length_mm'))
        elif comp.kind == 'camera':
            comp.sensor_width_mm = float(form.get('sensor_width_mm'))
            comp.sensor_height_mm = float(form.get('sensor_height_mm'))
            comp.pixel_size_um = float(form.get('pixel_size_um'))
        elif comp.kind == 'reducer_extender':
            comp.factor = float(form.get('factor'))

        db.commit()
        flash(_("Component '%(component_name)s' updated successfully.", component_name=comp.name), "success")
    except Exception as e:
        db.rollback()
        flash(_("Error updating component: %(error)s", error=e), "error")
    return redirect(url_for('core.config_form'))

@tools_bp.route('/add_rig', methods=['POST'])
@login_required
def add_rig():
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        user = db.query(DbUser).filter_by(username=username).one()
        form = request.form
        rig_id = form.get('rig_id')

        tel_id = int(form.get('telescope_id'))
        cam_id = int(form.get('camera_id'))
        red_id_str = form.get('reducer_extender_id')
        red_id = int(red_id_str) if red_id_str else None

        # --- NEW LOGIC START ---
        # 1. Fetch the component objects needed for calculation
        tel_obj = db.get(Component, tel_id)
        cam_obj = db.get(Component, cam_id)
        red_obj = db.get(Component, red_id) if red_id else None

        # 2. Calculate the derived properties (EFL, f-ratio, scale, FOV)
        efl, f_ratio, scale, fov_w = _compute_rig_metrics_from_components(tel_obj, cam_obj, red_obj)
        fov_h = None
        if cam_obj and cam_obj.sensor_height_mm and efl:
            try:
                # Calculate FOV height using the derived effective focal length
                fov_h = (degrees(2 * atan((cam_obj.sensor_height_mm / 2.0) / efl)) * 60.0)
            except:
                pass

        if rig_id:  # Update
            rig = db.get(Rig, int(rig_id))
            rig.rig_name = form.get('rig_name')
            rig.telescope_id, rig.camera_id, rig.reducer_extender_id = tel_id, cam_id, red_id
            # Guide optics FK fields and OAG flag
            rig.guide_telescope_id = safe_int(form.get('guide_telescope_id'))
            rig.guide_camera_id = safe_int(form.get('guide_camera_id'))
            rig.guide_is_oag = form.get('guide_is_oag') == 'on'
            flash(_("Rig '%(rig_name)s' updated successfully.", rig_name=rig.rig_name), "success")
        else:  # Add
            new_rig = Rig(
                user_id=user.id, rig_name=form.get('rig_name'),
                telescope_id=tel_id, camera_id=cam_id, reducer_extender_id=red_id,
                # Guide optics FK fields and OAG flag
                guide_telescope_id=safe_int(form.get('guide_telescope_id')),
                guide_camera_id=safe_int(form.get('guide_camera_id')),
                guide_is_oag=form.get('guide_is_oag') == 'on'
            )
            db.add(new_rig)
            rig = new_rig  # Reference the new object for update below
            flash(_("Rig '%(rig_name)s' created successfully.", rig_name=new_rig.rig_name), "success")

        # 3. Persist calculated values to the Rig object (for both ADD and UPDATE)
        rig.effective_focal_length = efl
        rig.f_ratio = f_ratio
        rig.image_scale = scale
        rig.fov_w_arcmin = fov_w
        # NOTE: fov_w_arcmin is used for width, but we should store the height too for a complete record
        # Although the Rig model currently only has fov_w_arcmin, we'll ensure we persist the calculated values.
        # Since the model is missing fov_h_arcmin, we'll only persist the existing fields:
        # We need to verify if fov_h_arcmin was added in a previous step, if not, we stick to fov_w_arcmin.
        # The provided JournalSession model (line 343) shows rig_fov_h_snapshot, so we assume the Rig model
        # was intended to have it. However, since it is not visible, we only update the ones we know exist:

        # --- NEW LOGIC END ---

        db.commit()
    except Exception as e:
        db.rollback()
        flash(_("Error saving rig: %(error)s", error=e), "error")
    return redirect(url_for('core.config_form'))

@tools_bp.route('/delete_component', methods=['POST'])
@login_required
def delete_component():
    db = get_db()
    try:
        comp_id = int(request.form.get('component_id'))
        # Check if component is in use by any rig for this user
        in_use = db.query(Rig).filter(
            (Rig.telescope_id == comp_id) |
            (Rig.camera_id == comp_id) |
            (Rig.reducer_extender_id == comp_id)
        ).first()

        if in_use:
            flash(_("Cannot delete component: It is used in at least one rig."), "error")
        else:
            comp_to_delete = db.get(Component, comp_id)
            db.delete(comp_to_delete)
            db.commit()
            flash(_("Component deleted successfully."), "success")
    except Exception as e:
        db.rollback()
        flash(_("Error deleting component: %(error)s", error=e), "error")
    return redirect(url_for('core.config_form'))

@tools_bp.route('/delete_rig', methods=['POST'])
@login_required
def delete_rig():
    db = get_db()
    try:
        rig_id = int(request.form.get('rig_id'))
        rig_to_delete = db.get(Rig, rig_id)
        db.delete(rig_to_delete)
        db.commit()
        flash(_("Rig deleted successfully."), "success")
    except Exception as e:
        db.rollback()
        flash(_("Error deleting rig: %(error)s", error=e), "error")
    return redirect(url_for('core.config_form'))

@tools_bp.route('/set_rig_sort_preference', methods=['POST'])
@login_required
def set_rig_sort_preference():
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        user = db.query(DbUser).filter_by(username=username).one()
        prefs = db.query(UiPref).filter_by(user_id=user.id).first()
        if not prefs:
            prefs = UiPref(user_id=user.id, json_blob='{}')
            db.add(prefs)

        try:
            settings = json.loads(prefs.json_blob or '{}')
        except json.JSONDecodeError:
            settings = {}

        sort_value = request.get_json(force=True).get('sort', 'name-asc')
        settings['rig_sort'] = sort_value
        prefs.json_blob = json.dumps(settings)

        db.commit()
        return jsonify({"status": "ok", "sort_order": sort_value})
    except Exception as e:
        db.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500

@tools_bp.route('/get_rig_data')
@login_required
def get_rig_data():
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    user = db.query(DbUser).filter_by(username=username).one()

    # Fetch all components for the user
    components = db.query(Component).filter_by(user_id=user.id).all()
    telescopes = [c for c in components if c.kind == 'telescope']
    cameras = [c for c in components if c.kind == 'camera']
    reducers = [c for c in components if c.kind == 'reducer_extender']

    # Fetch all rigs and their related components eagerly
    rigs_from_db = db.query(Rig).filter_by(user_id=user.id).all()

    # Assemble the data structure for the frontend
    components_dict = {
        "telescopes": [
            {
                "id": c.id, "name": c.name, "aperture_mm": c.aperture_mm, "focal_length_mm": c.focal_length_mm,
                "is_shared": c.is_shared, "original_user_id": c.original_user_id  # <-- ADDED
            } for c in telescopes
        ],
        "cameras": [
            {
                "id": c.id, "name": c.name, "sensor_width_mm": c.sensor_width_mm,
                "sensor_height_mm": c.sensor_height_mm, "pixel_size_um": c.pixel_size_um,
                "is_shared": c.is_shared, "original_user_id": c.original_user_id  # <-- ADDED
            } for c in cameras
        ],
        "reducers_extenders": [
            {
                "id": c.id, "name": c.name, "factor": c.factor,
                "is_shared": c.is_shared, "original_user_id": c.original_user_id  # <-- ADDED
            } for c in reducers
        ]
    }

    rigs_list = []
    for r in rigs_from_db:
        # Use the already fetched components to calculate rig data
        tel_obj = next((c for c in telescopes if c.id == r.telescope_id), None)
        cam_obj = next((c for c in cameras if c.id == r.camera_id), None)
        red_obj = next((c for c in reducers if c.id == r.reducer_extender_id), None)
        efl, f_ratio, scale, fov_w = _compute_rig_metrics_from_components(tel_obj, cam_obj, red_obj)
        fov_h = (degrees(2 * atan((cam_obj.sensor_height_mm / 2.0) / efl)) * 60.0) if cam_obj and cam_obj.sensor_height_mm and efl else None

        # Resolve guide optics FK references
        guide_tel_obj = next((c for c in telescopes if c.id == r.guide_telescope_id), None) if r.guide_telescope_id else None
        guide_cam_obj = next((c for c in cameras if c.id == r.guide_camera_id), None) if r.guide_camera_id else None

        # Calculate dither recommendation if guide optics are configured
        dither_rec = None
        guide_fl = None
        guide_pixel_size = None

        # Determine guide focal length based on OAG setting
        if r.guide_is_oag:
            # OAG uses main scope focal length
            guide_fl = efl  # effective focal length already accounts for reducer/extender
        elif guide_tel_obj and guide_tel_obj.focal_length_mm:
            guide_fl = guide_tel_obj.focal_length_mm

        # Get guide camera pixel size
        if guide_cam_obj and guide_cam_obj.pixel_size_um:
            guide_pixel_size = guide_cam_obj.pixel_size_um

        # Calculate dither if we have all required values
        if (cam_obj and cam_obj.pixel_size_um and efl and
            guide_pixel_size and guide_fl):
            dither_rec = calculate_dither_recommendation(
                main_pixel_size_um=cam_obj.pixel_size_um,
                main_focal_length_mm=efl,
                guide_pixel_size_um=guide_pixel_size,
                guide_focal_length_mm=guide_fl,
                desired_main_shift_px=DEFAULT_DITHER_MAIN_SHIFT_PX
            )

        rigs_list.append({
            "rig_id": r.id, "rig_name": r.rig_name,
            "telescope_id": r.telescope_id, "camera_id": r.camera_id, "reducer_extender_id": r.reducer_extender_id,
            "effective_focal_length": efl, "f_ratio": f_ratio,
            "image_scale": scale, "fov_w_arcmin": fov_w, "fov_h_arcmin": fov_h,
            # Main equipment names for display
            "telescope_name": tel_obj.name if tel_obj else None,
            "camera_name": cam_obj.name if cam_obj else None,
            "reducer_name": red_obj.name if red_obj else None,
            # Guide optics FK fields and OAG flag
            "guide_telescope_id": r.guide_telescope_id,
            "guide_camera_id": r.guide_camera_id,
            "guide_is_oag": r.guide_is_oag,
            # Resolved guide equipment names for display
            "guide_telescope_name": guide_tel_obj.name if guide_tel_obj else None,
            "guide_camera_name": guide_cam_obj.name if guide_cam_obj else None,
            # Dither recommendation (None if guide optics not configured)
            "dither_recommendation": dither_rec
        })

    # Get sorting preference from UiPref
    prefs = db.query(UiPref).filter_by(user_id=user.id).one_or_none()
    sort_preference = 'name-asc'
    if prefs and prefs.json_blob:
        try:
            sort_preference = json.loads(prefs.json_blob).get('rig_sort', 'name-asc')
        except json.JSONDecodeError: pass

    sorted_rigs = sort_rigs(rigs_list, sort_preference)

    return jsonify({
        "components": components_dict,
        "rigs": sorted_rigs,
        "sort_preference": sort_preference
    })

@tools_bp.route('/download_config')
@login_required
def download_config():
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        u = db.query(DbUser).filter_by(username=username).one_or_none()
        if not u:
            flash(_("User not found."), "error")
            return redirect(url_for('core.config_form'))

        # --- 1. Load base settings from UiPref ---
        config_doc = {}
        prefs = db.query(UiPref).filter_by(user_id=u.id).first()
        if prefs and prefs.json_blob:
            try:
                config_doc = json.loads(prefs.json_blob)
            except json.JSONDecodeError:
                pass  # Start with empty doc if JSON is corrupt

        # --- 2. Load Locations ---
        locs = db.query(Location).options(selectinload(Location.horizon_points)).filter_by(user_id=u.id).all()
        default_loc_name = next((l.name for l in locs if l.is_default), None)
        config_doc["default_location"] = default_loc_name
        config_doc["locations"] = {
            l.name: {
                **{
                    "lat": l.lat, "lon": l.lon, "timezone": l.timezone,
                    "altitude_threshold": l.altitude_threshold,
                    "active": l.active,
                    "comments": l.comments,
                    "horizon_mask": [[hp.az_deg, hp.alt_min_deg] for hp in sorted(l.horizon_points, key=lambda p: p.az_deg)]
                },
                **({"bortle_scale": l.bortle_scale} if l.bortle_scale is not None else {})
            } for l in locs
        }

        # --- 3. Load Objects ---
        db_objects = db.query(AstroObject).filter_by(user_id=u.id).order_by(AstroObject.object_name).all()
        config_doc["objects"] = [o.to_dict() for o in db_objects]

        # --- 4. Load Saved Framings (NEW) ---
        saved_framings_db = db.query(SavedFraming).filter_by(user_id=u.id).all()
        saved_framings_list = []
        for sf in saved_framings_db:
            # Resolve rig name for portability (ID is local to DB)
            r_name = None
            if sf.rig_id:
                rig_obj = db.get(Rig, sf.rig_id)
                if rig_obj: r_name = rig_obj.rig_name

            saved_framings_list.append({
                "object_name": sf.object_name,
                "rig_name": r_name,
                "ra": sf.ra,
                "dec": sf.dec,
                "rotation": sf.rotation,
                "survey": sf.survey,
                "blend_survey": sf.blend_survey,
                "blend_opacity": sf.blend_opacity
            })
        config_doc["saved_framings"] = saved_framings_list

        # --- 5. Load Saved Views ---
        db_views = db.query(SavedView).filter_by(user_id=u.id).order_by(SavedView.name).all()
        config_doc["saved_views"] = [
            {
                "name": v.name,
                "description": v.description,
                "is_shared": v.is_shared,
                "settings": json.loads(v.settings_json)
            } for v in db_views
        ]

        # --- 6. Create in-memory file ---
        yaml_string = yaml.dump(config_doc, sort_keys=False, allow_unicode=True, indent=2, default_flow_style=False)
        str_io = io.BytesIO(yaml_string.encode('utf-8'))

        # Determine filename
        if SINGLE_USER_MODE:
            download_name = "config_default.yaml"
        else:
            download_name = f"config_{username}.yaml"

        return send_file(str_io, as_attachment=True, download_name=download_name, mimetype='text/yaml')

    except Exception as e:
        db.rollback()
        flash(_("Error generating config file: %(error)s", error=e), "error")
        traceback.print_exc()  # Log the full error to the console
        return redirect(url_for('core.config_form'))

@tools_bp.route('/download_journal')
@login_required
def download_journal():
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        u = db.query(DbUser).filter_by(username=username).one_or_none()
        if not u:
            flash(_("User not found."), "error")
            return redirect(url_for('core.config_form'))

        # --- 1. Load Projects (Including new fields) ---
        projects = db.query(Project).filter_by(user_id=u.id).order_by(Project.name).all()
        projects_list = [
            {
                "project_id": p.id,
                "project_name": p.name,
                "target_object_id": p.target_object_name,
                "description_notes": p.description_notes,
                "framing_notes": p.framing_notes,
                "processing_notes": p.processing_notes,
                "final_image_file": p.final_image_file,
                "goals": p.goals,
                "status": p.status,
            } for p in projects
        ]

        # --- 2. Load Sessions ---
        sessions = db.query(JournalSession).filter_by(user_id=u.id).order_by(JournalSession.date_utc.asc()).all()
        sessions_list = []

        for s in sessions:
            sessions_list.append({
                "session_id": s.external_id or s.id,
                "project_id": s.project_id,
                "session_date": s.date_utc.isoformat(),
                "target_object_id": s.object_name,
                "general_notes_problems_learnings": s.notes,
                "session_image_file": s.session_image_file,
                "location_name": s.location_name,
                "seeing_observed_fwhm": s.seeing_observed_fwhm,
                "sky_sqm_observed": s.sky_sqm_observed,
                "moon_illumination_session": s.moon_illumination_session,
                "moon_angular_separation_session": s.moon_angular_separation_session,
                "weather_notes": s.weather_notes,
                "telescope_setup_notes": s.telescope_setup_notes,
                "filter_used_session": s.filter_used_session,
                "guiding_rms_avg_arcsec": s.guiding_rms_avg_arcsec,
                "guiding_equipment": s.guiding_equipment,
                "dither_details": s.dither_details,
                "dither_pixels": s.dither_pixels,
                "dither_every_n": s.dither_every_n,
                "dither_notes": s.dither_notes,
                "acquisition_software": s.acquisition_software,
                "gain_setting": s.gain_setting,
                "offset_setting": s.offset_setting,
                "camera_temp_setpoint_c": s.camera_temp_setpoint_c,
                "camera_temp_actual_avg_c": s.camera_temp_actual_avg_c,
                "binning_session": s.binning_session,
                "darks_strategy": s.darks_strategy,
                "flats_strategy": s.flats_strategy,
                "bias_darkflats_strategy": s.bias_darkflats_strategy,
                "session_rating_subjective": s.session_rating_subjective,
                "transparency_observed_scale": s.transparency_observed_scale,
                "number_of_subs_light": s.number_of_subs_light,
                "exposure_time_per_sub_sec": s.exposure_time_per_sub_sec,
                "filter_L_subs": s.filter_L_subs, "filter_L_exposure_sec": s.filter_L_exposure_sec,
                "filter_R_subs": s.filter_R_subs, "filter_R_exposure_sec": s.filter_R_exposure_sec,
                "filter_G_subs": s.filter_G_subs, "filter_G_exposure_sec": s.filter_G_exposure_sec,
                "filter_B_subs": s.filter_B_subs, "filter_B_exposure_sec": s.filter_B_exposure_sec,
                "filter_Ha_subs": s.filter_Ha_subs, "filter_Ha_exposure_sec": s.filter_Ha_exposure_sec,
                "filter_OIII_subs": s.filter_OIII_subs, "filter_OIII_exposure_sec": s.filter_OIII_exposure_sec,
                "filter_SII_subs": s.filter_SII_subs, "filter_SII_exposure_sec": s.filter_SII_exposure_sec,
                "calculated_integration_time_minutes": s.calculated_integration_time_minutes,
                "rig_id_snapshot": s.rig_id_snapshot,  # <-- ADDED
                "rig_name_snapshot": s.rig_name_snapshot,
                "rig_efl_snapshot": s.rig_efl_snapshot,
                "rig_fr_snapshot": s.rig_fr_snapshot,
                "rig_scale_snapshot": s.rig_scale_snapshot,
                "rig_fov_w_snapshot": s.rig_fov_w_snapshot,
                "rig_fov_h_snapshot": s.rig_fov_h_snapshot,
                "telescope_name_snapshot": s.telescope_name_snapshot,
                "reducer_name_snapshot": s.reducer_name_snapshot,
                "camera_name_snapshot": s.camera_name_snapshot,
                "custom_filter_data": s.custom_filter_data,
                "asiair_log_content": s.asiair_log_content,
                "phd2_log_content": s.phd2_log_content,
                "log_analysis_cache": s.log_analysis_cache,
            })

        # --- 3. Load Custom Filter Definitions ---
        custom_filters_db = db.query(UserCustomFilter).filter_by(user_id=u.id).order_by(UserCustomFilter.created_at).all()
        custom_filters_list = [
            {'key': cf.filter_key, 'label': cf.filter_label}
            for cf in custom_filters_db
        ]

        journal_doc = {
            "projects": projects_list,
            "custom_mono_filters": custom_filters_list,
            "sessions": sessions_list
        }

        # --- 3. Create in-memory file ---
        yaml_string = yaml.dump(journal_doc, sort_keys=False, allow_unicode=True, indent=2, default_flow_style=False)
        str_io = io.BytesIO(yaml_string.encode('utf-8'))

        # Determine filename
        if SINGLE_USER_MODE:
            download_name = "journal_default.yaml"
        else:
            download_name = f"journal_{username}.yaml"

        return send_file(str_io, as_attachment=True, download_name=download_name, mimetype='text/yaml')

    except Exception as e:
        db.rollback()
        flash(_("Error generating journal file: %(error)s", error=e), "error")
        traceback.print_exc()  # Log the full error to the console
        return redirect(url_for('core.config_form'))

@tools_bp.route('/import_journal', methods=['POST'])
@login_required
def import_journal():
    if 'file' not in request.files:
        flash(_("No file selected for journal import."), "error")
        return redirect(url_for('core.config_form'))

    file = request.files['file']
    if file.filename == '':
        flash(_("No file selected for journal import."), "error")
        return redirect(url_for('core.config_form'))

    if file and file.filename.endswith('.yaml'):
        try:
            contents = file.read().decode('utf-8')
            new_journal_data = yaml.safe_load(contents)

            if new_journal_data is None:
                new_journal_data = {"projects": [], "sessions": []}  # Handle empty file

            # Basic validation
            is_valid, message = validate_journal_data(new_journal_data)
            if not is_valid:
                flash(_("Invalid journal file structure: %(message)s", message=message), "error")
                return redirect(url_for('core.config_form'))

            username = "default" if SINGLE_USER_MODE else current_user.username

            # === START REFACTOR: WIPE & REPLACE ===
            db = get_db()
            try:
                user = _upsert_user(db, username)

                # 1. Wipe existing Journal Data
                # We must delete Sessions first (they depend on Projects), then Projects.
                print(f"[IMPORT_JOURNAL] Wiping existing sessions and projects for user '{username}'...")

                db.query(JournalSession).filter_by(user_id=user.id).delete()
                # Projects are safe to delete after sessions are gone
                db.query(Project).filter_by(user_id=user.id).delete()

                db.flush()  # Ensure deletion happens before insertion

                # 2. Import New Data
                _migrate_journal(db, user, new_journal_data)

                db.commit()
                flash(_("Journal imported successfully! (Previous journal data was replaced)"), "success")
            except Exception as e:
                db.rollback()
                print(f"[IMPORT_JOURNAL] DB Error: {e}")
                # Re-raise to hit the outer exception handler for detailed logging
                raise e
            # === END REFACTOR ===

            return redirect(url_for('core.config_form'))

        except yaml.YAMLError as ye:
            print(f"[IMPORT JOURNAL ERROR] Invalid YAML format: {ye}")
            flash(_("Import failed: Invalid YAML format in the journal file. %(error)s", error=ye), "error")
            return redirect(url_for('core.config_form'))
        except Exception as e:
            print(f"[IMPORT JOURNAL ERROR] {e}")
            # Clean up the error message for display
            err_msg = str(e)
            if "UNIQUE constraint failed" in err_msg:
                err_msg = "Data conflict detected. Please try again (the wipe logic should prevent this)."
            flash(_("Import failed: %(error)s", error=err_msg), "error")
            return redirect(url_for('core.config_form'))
    else:
        flash(_("Invalid file type. Please upload a .yaml journal file."), "error")
        return redirect(url_for('core.config_form'))

@tools_bp.route('/import_config', methods=['POST'])
@login_required
def import_config():
    from nova import update_outlook_cache
    if SINGLE_USER_MODE:
        username = "default"
    elif current_user.is_authenticated:
        username = current_user.username
    else:
        flash(_("Authentication error during import."), "error")
        return redirect(url_for('core.login'))

    try:
        if 'file' not in request.files:
            flash(_("No file selected for import."), "error")
            return redirect(url_for('core.config_form'))

        file = request.files['file']
        if file.filename == '':
            flash(_("No file selected for import."), "error")
            return redirect(url_for('core.config_form'))

        contents = file.read().decode('utf-8')
        new_config = yaml.safe_load(contents)

        valid, errors = validate_config(new_config)
        if not valid:
            error_message = f"Configuration validation failed: {json.dumps(errors, indent=2)}"
            flash(error_message, "error")
            return redirect(url_for('core.config_form'))

        # === START REFACTOR: FULL WIPE & REPLACE ===
        db = get_db()
        try:
            user = _upsert_user(db, username)

            print(f"[IMPORT_CONFIG] Wiping all existing config data for user '{username}'...")

            # Guard: Ensure import has at least one location before wiping existing ones
            imported_locations = new_config.get("locations", {})
            if not imported_locations or len(imported_locations) == 0:
                flash(_("Cannot import: Configuration must contain at least one location."), "error")
                return redirect(url_for('core.config_form'))

            # Guard: Ensure at least one imported location is active
            has_active_location = any(
                loc_data.get('active', True) for loc_data in imported_locations.values()
            )
            if not has_active_location:
                flash(_("Cannot import: Configuration must contain at least one active location."), "error")
                return redirect(url_for('core.config_form'))

            # 1. Delete existing locations (only if import has locations)
            db.query(Location).filter_by(user_id=user.id).delete()

            # 2. Delete existing objects
            db.query(AstroObject).filter_by(user_id=user.id).delete()

            # 3. Delete existing saved views
            db.query(SavedView).filter_by(user_id=user.id).delete()

            # 4. Delete existing saved framings (ADDED THIS)
            db.query(SavedFraming).filter_by(user_id=user.id).delete()

            # 5. Flush deletions
            db.flush()

            # 6. Import New Data
            _migrate_locations(db, user, new_config)
            _migrate_objects(db, user, new_config)
            _migrate_ui_prefs(db, user, new_config)
            _migrate_saved_views(db, user, new_config)

            # 7. Import Saved Framings
            _migrate_saved_framings(db, user, new_config)

            # Capture ID for thread
            user_id_for_thread = user.id

            db.commit()
            flash(_("Config imported successfully! (Previous config was replaced)"), "success")
        except Exception as e:
            db.rollback()
            print(f"[IMPORT_CONFIG] DB Error: {e}")
            raise e
        # === END REFACTOR ===

        # Trigger background cache update for ACTIVE locations in the import
        # (Force refresh to ensure active projects match the new config)
        user_config_for_thread = new_config.copy()

        # Determine sampling interval from the imported config if possible, else fallback
        import_interval = 15
        if SINGLE_USER_MODE:
            import_interval = user_config_for_thread.get('sampling_interval_minutes') or 15

        locations_in_import = user_config_for_thread.get('locations', {})

        for loc_name, loc_data in locations_in_import.items():
            # FILTER: Skip inactive locations to prevent CPU spikes
            if not loc_data.get('active', True):
                continue

            # 1. Standardize filename construction (Matches Master Cache)
            user_log_key = get_user_log_string(user_id_for_thread, username)
            safe_log_key = user_log_key.replace(" | ", "_").replace(".", "").replace(" ", "_")
            loc_safe = loc_name.lower().replace(' ', '_')

            status_key = f"({user_log_key})_{loc_name}"
            cache_filename = os.path.join(CACHE_DIR, f"outlook_cache_{safe_log_key}_{loc_safe}.json")

            # 2. REMOVED 'if not os.path.exists': Always force update on import

            thread = threading.Thread(target=update_outlook_cache,
                                      args=(user_id_for_thread, status_key, cache_filename, loc_name,
                                            user_config_for_thread,
                                            import_interval, None))
            thread.start()

        return redirect(url_for('core.config_form'))

    except yaml.YAMLError as ye:
        flash(_("Import failed: Invalid YAML. (%(error)s)", error=ye), "error")
        return redirect(url_for('core.config_form'))
    except Exception as e:
        flash(_("Import failed: %(error)s", error=str(e)), "error")
        return redirect(url_for('core.config_form'))

@tools_bp.route('/import_catalog/<pack_id>', methods=['POST'])
@login_required
def import_catalog(pack_id):
    """Import a server-side catalog pack into the current user's object library.

    This is non-destructive: existing objects are never overwritten. If an
    object with the same name already exists, it is skipped (aside from
    catalog_sources bookkeeping handled in the helper).
    """
    # Determine which username to use for DB lookups
    try:
        single_user_mode = bool(globals().get('SINGLE_USER_MODE', True))
    except Exception:
        single_user_mode = True

    if single_user_mode:
        username = "default"
    else:
        if not current_user.is_authenticated:
            flash(_("Authentication error during catalog import."), "error")
            return redirect(url_for('core.login'))
        username = current_user.username

    db = get_db()
    try:
        user = _upsert_user(db, username)

        catalog_data, meta = load_catalog_pack(pack_id)
        if not catalog_data or not isinstance(catalog_data, dict):
            flash(_("Catalog pack not found or invalid."), "error")
            return redirect(url_for('core.config_form'))

        created, enriched, skipped = import_catalog_pack_for_user(db, user, catalog_data, pack_id)
        db.commit()

        pack_name = (meta or {}).get("name") or pack_id
        msg = f"Catalog '{pack_name}': {created} new, {enriched} enriched (updated), {skipped} skipped."
        flash(msg, "success")
    except Exception as e:
        db.rollback()
        print(f"[CATALOG IMPORT] Error importing catalog pack '{pack_id}': {e}")
        flash(_("Catalog import failed due to an internal error."), "error")

    return redirect(url_for('core.config_form'))

@tools_bp.route('/download_rig_config')
@login_required
def download_rig_config():
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        u = db.query(DbUser).filter_by(username=username).one_or_none()
        if not u:
            flash(_("User not found."), "error")
            return redirect(url_for('core.config_form'))

        # --- Generate rigs doc from DB ---
        comps = db.query(Component).filter_by(user_id=u.id).all()
        rigs = db.query(Rig).filter_by(user_id=u.id).order_by(Rig.rig_name).all()

        def bykind(k):
            return [c for c in comps if c.kind == k]

        rigs_doc = {
            "components": {
                "telescopes": [
                    {"id": c.id, "name": c.name, "aperture_mm": c.aperture_mm, "focal_length_mm": c.focal_length_mm,
                     # --- ADD THESE 3 LINES ---
                     "is_shared": c.is_shared, "original_user_id": c.original_user_id,
                     "original_item_id": c.original_item_id
                     }
                    for c in bykind("telescope")
                ],
                "cameras": [
                    {"id": c.id, "name": c.name, "sensor_width_mm": c.sensor_width_mm,
                     "sensor_height_mm": c.sensor_height_mm, "pixel_size_um": c.pixel_size_um,
                     # --- ADD THESE 3 LINES ---
                     "is_shared": c.is_shared, "original_user_id": c.original_user_id,
                     "original_item_id": c.original_item_id
                     }
                    for c in bykind("camera")
                ],
                "reducers_extenders": [
                    {"id": c.id, "name": c.name, "factor": c.factor,
                     # --- ADD THESE 3 LINES ---
                     "is_shared": c.is_shared, "original_user_id": c.original_user_id,
                     "original_item_id": c.original_item_id
                     }
                    for c in bykind("reducer_extender")
                ],
            },
            "rigs": []  # We will populate this next
        }

        # --- Calculate metrics for each rig ---
        final_rigs_list = []
        for r in rigs:
            tel_obj = next((c for c in comps if c.id == r.telescope_id), None)
            cam_obj = next((c for c in comps if c.id == r.camera_id), None)
            red_obj = next((c for c in comps if c.id == r.reducer_extender_id), None)
            guide_tel_obj = next((c for c in comps if c.id == r.guide_telescope_id), None)
            guide_cam_obj = next((c for c in comps if c.id == r.guide_camera_id), None)

            efl, f_ratio, scale, fov_w = _compute_rig_metrics_from_components(tel_obj, cam_obj, red_obj)

            final_rigs_list.append({
                "rig_id": r.id,  # Legacy: kept for backward compatibility
                "rig_name": r.rig_name,
                "telescope_name": tel_obj.name if tel_obj else None,  # Natural key
                "camera_name": cam_obj.name if cam_obj else None,  # Natural key
                "reducer_extender_name": red_obj.name if red_obj else None,  # Natural key
                "telescope_id": r.telescope_id,  # Legacy: kept for backward compatibility
                "camera_id": r.camera_id,  # Legacy: kept for backward compatibility
                "reducer_extender_id": r.reducer_extender_id,  # Legacy: kept for backward compatibility
                "effective_focal_length": efl,
                "f_ratio": f_ratio,
                "image_scale": scale,
                "fov_w_arcmin": fov_w,
                # Guiding equipment
                "guide_telescope_name": guide_tel_obj.name if guide_tel_obj else None,
                "guide_camera_name": guide_cam_obj.name if guide_cam_obj else None,
                "guide_telescope_id": r.guide_telescope_id,
                "guide_camera_id": r.guide_camera_id,
                "guide_is_oag": r.guide_is_oag
            })

        rigs_doc["rigs"] = final_rigs_list  # Add the populated list to the doc

        # --- Create in-memory file ---
        yaml_string = yaml.dump(rigs_doc, sort_keys=False, allow_unicode=True)
        str_io = io.BytesIO(yaml_string.encode('utf-8'))

        # Determine filename
        if SINGLE_USER_MODE:
            download_name = "rigs_default.yaml"
        else:
            download_name = f"rigs_{username}.yaml"

        return send_file(str_io, as_attachment=True, download_name=download_name, mimetype='text/yaml')

    except Exception as e:
        db.rollback()
        flash(_("Error generating rig config: %(error)s", error=e), "error")
        traceback.print_exc()  # Log the full error to the console
        return redirect(url_for('core.config_form'))

@tools_bp.route('/download_journal_photos')
@login_required
def download_journal_photos():
    """
    Finds all journal images for the current user, creates a ZIP file in memory,
    and sends it as a download.
    """
    if SINGLE_USER_MODE:
        username = "default"
    else:
        username = current_user.username

    user_upload_dir = os.path.join(UPLOAD_FOLDER, username)

    # Check if the user's upload directory exists and has files
    if not os.path.isdir(user_upload_dir):
        flash(_("No journal photos found to download."), "info")
        return redirect(url_for('core.config_form'))

    # Use an in-memory buffer to build the ZIP file without writing to disk
    memory_file = io.BytesIO()

    with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
        # Walk through the user's directory and add all files to the ZIP
        for root, dirs, files in os.walk(user_upload_dir):
            for file in files:
                # Create the full path to the file
                file_path = os.path.join(root, file)
                # Add the file to the zip, using just the filename as the archive name
                zf.write(file_path, arcname=file)

    # After the 'with' block, the ZIP is built in memory_file.
    # Move the buffer's cursor to the beginning.
    memory_file.seek(0)

    # Create a dynamic filename for the download
    timestamp = datetime.now().strftime('%Y-%m-%d')
    download_name = f"nova_journal_photos_{username}_{timestamp}.zip"

    # Send the in-memory file to the user
    return send_file(
        memory_file,
        mimetype='application/zip',
        as_attachment=True,
        download_name=download_name
    )

@tools_bp.route('/import_journal_photos', methods=['POST'])
@login_required
def import_journal_photos():
    """
    Handles the upload of a ZIP archive and safely extracts its contents
    into the user's upload directory.

    V2 FIX: This version strips all directory structures from the ZIP,
    placing all files flatly into the user's root upload directory.
    This correctly handles migrating from a multi-user (e.g., /uploads/mrantonSG/)
    to a single-user (/uploads/default/) instance.
    """
    if 'file' not in request.files:
        flash(_("No file selected for photo import."), "error")
        return redirect(url_for('core.config_form'))

    file = request.files['file']
    if file.filename == '' or not file.filename.lower().endswith('.zip'):
        flash(_("Please select a valid .zip file to import."), "error")
        return redirect(url_for('core.config_form'))

    if SINGLE_USER_MODE:
        username = "default"
    else:
        username = current_user.username

    user_upload_dir = os.path.join(UPLOAD_FOLDER, username)
    os.makedirs(user_upload_dir, exist_ok=True)  # Ensure the destination exists

    try:
        if not zipfile.is_zipfile(file):
            flash(_("Import failed: The uploaded file is not a valid ZIP archive."), "error")
            return redirect(url_for('core.config_form'))

        file.seek(0)

        extracted_count = 0
        with zipfile.ZipFile(file, 'r') as zf:
            for member in zf.infolist():
                # Skip directories
                if member.is_dir():
                    continue

                # Get just the filename, stripping all parent directories
                filename = os.path.basename(member.filename)

                # Skip empty filenames (like .DS_Store or empty dir entries)
                if not filename:
                    continue

                # 🔒 Security Check: Prevent path traversal
                # (os.path.basename already helps, but we double-check)
                if ".." in filename or filename.startswith(("/", "\\")):
                    print(f"Skipping potentially malicious file: {member.filename}")
                    continue

                # Build the final, correct, flat target path
                target_path = os.path.join(user_upload_dir, filename)

                # Extract the file data
                with zf.open(member) as source, open(target_path, "wb") as target:
                    target.write(source.read())

                extracted_count += 1

        flash(_("Journal photos imported successfully! Extracted %(count)d files.", count=extracted_count), "success")

    except zipfile.BadZipFile:
        flash(_("Import failed: The ZIP file appears to be corrupted."), "error")
    except Exception as e:
        flash(_("An unexpected error occurred during import: %(error)s", error=e), "error")

    return redirect(url_for('core.config_form'))

@tools_bp.route('/import_rig_config', methods=['POST'])
@login_required
def import_rig_config():
    if 'file' not in request.files:
        flash(_("No file selected for rigs import."), "error")
        return redirect(url_for('core.config_form'))

    file = request.files['file']
    if not file or file.filename == '':
        flash(_("No file selected for rigs import."), "error")
        return redirect(url_for('core.config_form'))

    if file and file.filename.lower().endswith(('.yaml', '.yml')):
        try:
            new_rigs_data = yaml.safe_load(file.read().decode('utf-8'))
            if not isinstance(new_rigs_data, dict) or 'components' not in new_rigs_data or 'rigs' not in new_rigs_data:
                raise yaml.YAMLError("Invalid rigs file structure. Missing 'components' or 'rigs' keys.")

            username = "default" if SINGLE_USER_MODE else current_user.username

            # === START REFACTOR ===
            # Import directly into the database
            db = get_db()
            try:
                user = _upsert_user(db, username)  # Get or create the user in app.db
                print(f"[IMPORT_RIGS] Deleting all existing rigs and components for user '{username}' before import...")

                # 1. Delete existing Rigs (must be done first due to foreign keys)
                db.query(Rig).filter_by(user_id=user.id).delete()

                # 2. Delete existing Components
                db.query(Component).filter_by(user_id=user.id).delete()

                # 3. Flush the deletions
                db.flush()
                # Use the migration helper to load data directly into the DB
                _migrate_components_and_rigs(db, user, new_rigs_data, username)

                db.commit()
                flash(_("Rigs configuration imported and synced to database successfully!"), "success")
            except Exception as e:
                db.rollback()
                print(f"[IMPORT_RIGS] DB Error: {e}")
                raise e  # Re-throw to be caught by the outer block
            # === END REFACTOR ===

        except (yaml.YAMLError, Exception) as e:
            flash(_("Error importing rigs file: %(error)s", error=e), "error")

    else:
        flash(_("Invalid file type. Please upload a .yaml or .yml file."), "error")

    return redirect(url_for('core.config_form'))

@tools_bp.route('/upload_editor_image', methods=['POST'])
@login_required
def upload_editor_image():
    """
    Handles file uploads from the Trix editor.
    Saves the file to the user's upload directory and returns
    a JSON response with the file's public URL.
    """
    if 'file' not in request.files:
        return jsonify({"error": "No file part in request."}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "No selected file."}), 400

    # Determine username
    if SINGLE_USER_MODE:
        username = "default"
    else:
        username = current_user.username

    if file and allowed_file(file.filename):
        try:
            # Get the file extension
            file_extension = file.filename.rsplit('.', 1)[1].lower()

            # Generate a new, unique filename
            # e.g., "note_img_a1b2c3d4.jpg"
            new_filename = f"note_img_{uuid.uuid4().hex[:12]}.{file_extension}"

            # Create the user's upload directory if it doesn't exist
            user_upload_dir = os.path.join(UPLOAD_FOLDER, username)
            os.makedirs(user_upload_dir, exist_ok=True)

            # Save the file
            save_path = os.path.join(user_upload_dir, new_filename)
            file.save(save_path)

            # Create the public URL for the image
            # This must match the `get_uploaded_image` route
            public_url = url_for('core.get_uploaded_image', username=username, filename=new_filename)

            # Trix expects a JSON response with a 'url' key
            return jsonify({"url": public_url})

        except Exception as e:
            print(f"Error uploading editor image: {e}")
            return jsonify({"error": f"Server error during upload: {e}"}), 500

    return jsonify({"error": "File type not allowed."}), 400

@tools_bp.route("/tools/export/<username>", methods=["GET"])
@login_required
def export_yaml_for_user(username):
    # Only allow exporting self in multi-user; admin can export anyone (basic guard, adjust as needed)
    if not SINGLE_USER_MODE and current_user.username != username and current_user.username != "admin":
        flash(_("Not authorized to export another user's data."), "error")
        return redirect(url_for("core.index"))
    ok = export_user_to_yaml(username, out_dir=CONFIG_DIR)
    if not ok:
        flash(_("Export failed (no such user or empty data)."), "error")
        return redirect(url_for("core.index"))
    # Package into a ZIP so users get all three files at once
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    zip_name = f"nova_export_{username}_{ts}.zip"
    zip_path = os.path.join(INSTANCE_PATH, zip_name)
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        cfg_file = "config_default.yaml" if (SINGLE_USER_MODE and username == "default") else f"config_{username}.yaml"
        jrn_file = "journal_default.yaml" if (SINGLE_USER_MODE and username == "default") else f"journal_{username}.yaml"
        rigs_file = "rigs_default.yaml"
        for fn in [cfg_file, jrn_file, rigs_file]:
            full = os.path.join(CONFIG_DIR, fn)
            if os.path.exists(full):
                zf.write(full, arcname=fn)
    return send_file(zip_path, as_attachment=True, download_name=zip_name)

@tools_bp.route("/tools/import", methods=["POST"])
@login_required
def import_yaml_for_user():
    """
    Expect multipart form-data with fields:
      - username (optional in single-user; otherwise required)
      - config_file
      - rigs_file
      - journal_file
      - clear_existing = 'true'|'false'
    """
    username = request.form.get("username") or ("default" if SINGLE_USER_MODE else None)
    if not username:
        flash(_("Username is required in multi-user mode."), "error")
        return redirect(url_for("core.index"))

    # Basic guard: only allow importing for self unless admin
    if not SINGLE_USER_MODE and current_user.username != username and current_user.username != "admin":
        flash(_("Not authorized to import for another user."), "error")
        return redirect(url_for("core.index"))

    try:
        cfg = request.files.get("config_file")
        rigs = request.files.get("rigs_file")
        jrn = request.files.get("journal_file")
        if not (cfg and rigs and jrn):
            flash(_("Please provide config, rigs, and journal YAML files."), "error")
            return redirect(url_for("core.index"))

        # Persist to temp paths
        tmp_dir = os.path.join(INSTANCE_PATH, "tmp_import")
        os.makedirs(tmp_dir, exist_ok=True)
        cfg_path = os.path.join(tmp_dir, f"cfg_{uuid.uuid4().hex}.yaml")
        rigs_path = os.path.join(tmp_dir, f"rigs_{uuid.uuid4().hex}.yaml")
        jrn_path = os.path.join(tmp_dir, f"jrn_{uuid.uuid4().hex}.yaml")
        cfg.save(cfg_path); rigs.save(rigs_path); jrn.save(jrn_path)

        clear_existing = (request.form.get("clear_existing", "false").lower() == "true")
        ok = import_user_from_yaml(username, cfg_path, rigs_path, jrn_path, clear_existing=clear_existing)

        # Cleanup temp
        for p in [cfg_path, rigs_path, jrn_path]:
            try: os.remove(p)
            except Exception: pass

        if ok:
            flash(_("Import completed successfully!"), "success")
        else:
            flash(_("Import failed. See server logs for details."), "error")
    except Exception as e:
        print(f"[IMPORT] ERROR: {e}")
        flash(_("Import crashed. Check logs."), "error")
    return redirect(url_for("core.index"))

@tools_bp.route("/tools/repair_db", methods=["POST"])
@login_required
def repair_db_now():
    if not SINGLE_USER_MODE and current_user.username != "admin":
        flash(_("Not authorized."), "error")
        return redirect(url_for("core.index"))
    try:
        repair_journals(dry_run=False)
        flash(_("Database repair completed."), "success")
    except Exception as e:
        flash(_("Repair failed: %(error)s", error=e), "error")
    return redirect(url_for("core.index"))

@tools_bp.before_request
def csrf_protect_admin():
    if SINGLE_USER_MODE:
        return
    """Enforce CSRF on admin POST routes."""
    if request.method == "POST" and request.path.startswith("/admin/"):
        from nova import csrf
        csrf.protect()

@tools_bp.route("/admin/users")
@login_required
def admin_users():
    if SINGLE_USER_MODE:
        return redirect(url_for("core.index"))
    from nova import db, User
    if current_user.username != "admin":
        flash(_("Not authorized."), "error")
        return redirect(url_for("core.index"))
    users = db.session.scalars(db.select(User).order_by(User.id)).all()
    return render_template("admin_users.html", users=users)

@tools_bp.route("/admin/users/create", methods=["POST"])
@login_required
def admin_create_user():
    if SINGLE_USER_MODE:
        return redirect(url_for("core.index"))
    from nova import db, User
    if current_user.username != "admin":
        flash(_("Not authorized."), "error")
        return redirect(url_for("core.index"))
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    if not username or not password:
        flash(_("Username and password are required."), "error")
        return redirect(url_for("tools.admin_users"))
    if db.session.scalar(db.select(User).where(User.username == username)):
        flash(_("User '%(username)s' already exists.", username=username), "error")
        return redirect(url_for("tools.admin_users"))
    user = User(username=username)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    flash(_("User '%(username)s' created successfully.", username=username), "success")
    return redirect(url_for("tools.admin_users"))

@tools_bp.route("/admin/users/<int:user_id>/toggle", methods=["POST"])
@login_required
def admin_toggle_user(user_id):
    if SINGLE_USER_MODE:
        return redirect(url_for("core.index"))
    from nova import db, User
    if current_user.username != "admin":
        flash(_("Not authorized."), "error")
        return redirect(url_for("core.index"))
    user = db.session.get(User, user_id)
    if not user:
        flash(_("User not found."), "error")
        return redirect(url_for("tools.admin_users"))
    if user.username == "admin":
        flash(_("Cannot deactivate the admin account."), "error")
        return redirect(url_for("tools.admin_users"))
    user.active = not user.active
    db.session.commit()
    status = "activated" if user.active else "deactivated"
    flash(_("User '%(username)s' %(status)s.", username=user.username, status=status), "success")
    return redirect(url_for("tools.admin_users"))

@tools_bp.route("/admin/users/<int:user_id>/reset-password", methods=["POST"])
@login_required
def admin_reset_password(user_id):
    if SINGLE_USER_MODE:
        return redirect(url_for("core.index"))
    from nova import db, User
    if current_user.username != "admin":
        flash(_("Not authorized."), "error")
        return redirect(url_for("core.index"))
    user = db.session.get(User, user_id)
    if not user:
        flash(_("User not found."), "error")
        return redirect(url_for("tools.admin_users"))
    new_password = request.form.get("new_password", "")
    if not new_password:
        flash(_("Password cannot be empty."), "error")
        return redirect(url_for("tools.admin_users"))
    user.set_password(new_password)
    db.session.commit()
    flash(_("Password reset for '%(username)s'.", username=user.username), "success")
    return redirect(url_for("tools.admin_users"))

@tools_bp.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@login_required
def admin_delete_user(user_id):
    if SINGLE_USER_MODE:
        return redirect(url_for("core.index"))
    from nova import db, User
    if current_user.username != "admin":
        flash(_("Not authorized."), "error")
        return redirect(url_for("core.index"))
    user = db.session.get(User, user_id)
    if not user:
        flash(_("User not found."), "error")
        return redirect(url_for("tools.admin_users"))
    if user.username == "admin":
        flash(_("Cannot delete the admin account."), "error")
        return redirect(url_for("tools.admin_users"))
    uname = user.username
    db.session.delete(user)
    db.session.commit()
    flash(_("User '%(username)s' deleted.", username=uname), "success")
    return redirect(url_for("tools.admin_users"))
