"""
Nova DSO Tracker - Journal Blueprint
------------------------------------
Routes for journal session management: list, add, edit, duplicate, delete,
report generation, and CSV export.
"""

# =============================================================================
# Standard Library Imports
# =============================================================================
import os
import json
import uuid
import io
import csv
import traceback
from datetime import datetime
from math import atan, degrees

# =============================================================================
# Third-Party Imports
# =============================================================================
import bleach
from bleach.css_sanitizer import CSSSanitizer
from flask import (
    Blueprint, render_template, redirect, url_for, flash,
    request, g, make_response, session, jsonify
)
from flask_login import login_required, current_user
from flask_babel import gettext as _
from sqlalchemy.orm import selectinload

# =============================================================================
# Nova Package Imports (no circular import)
# =============================================================================
from nova import SINGLE_USER_MODE  # Import from nova for test patching compatibility
from nova.config import UPLOAD_FOLDER
from nova.models import (
    DbUser, Project, JournalSession, Rig, Component,
    AstroObject, UserCustomFilter
)
from nova.helpers import (
    get_db, allowed_file, safe_float, safe_int,
    save_log_to_filesystem, read_log_content, dither_display,
    # Moved from nova.__init__ for clean imports
    load_full_astro_context, generate_session_id,
    _compute_rig_metrics_from_components, get_ra_dec
)
from nova.analytics import record_event
from nova.report_graphs import generate_session_charts


# =============================================================================
# Blueprint Definition
# =============================================================================
journal_bp = Blueprint('journal', __name__)


# =============================================================================
# Journal Routes
# =============================================================================

@journal_bp.route('/journal')
@login_required
def journal_list_view():
    load_full_astro_context()
    db = get_db()
        # 1. Use the pre-loaded g.db_user (from the consolidated before_request)
    if not g.db_user:
        flash(_("User session error, please log in again."), "error")
        return redirect(url_for('core.login'))

    user_id = g.db_user.id

    # 2. Query only for the sessions for this user
    sessions = db.query(JournalSession).filter_by(user_id=user_id).order_by(JournalSession.date_utc.desc()).all()

    # 3. Use the pre-loaded g.alternative_names map for common names
    #    This avoids a second DB query for AstroObject.
    #    The map is { "m31": "Andromeda Galaxy", ... }
    object_names_lookup = g.alternative_names or {}

    # 4. Add the common name to each session object for the template
    for s in sessions:
        # Safely look up the common name using the lowercase object name
        s.target_common_name = object_names_lookup.get(
            s.object_name.lower() if s.object_name else '',
            s.object_name  # Fallback to the object_name itself if not found
        )

    record_event('journal_open')
    return render_template('journal_list.html', journal_sessions=sessions)


@journal_bp.route('/journal/add', methods=['GET', 'POST'])
@login_required
def journal_add():
    load_full_astro_context()
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    user = db.query(DbUser).filter_by(username=username).one()

    if request.method == 'POST':
        try:
            # --- START FIX: Validate the date ---
            session_date_str = request.form.get("session_date")
            try:
                parsed_date_utc = datetime.strptime(session_date_str, '%Y-%m-%d').date()
            except (ValueError, TypeError):
                # This is the new error handling: flash and redirect
                flash(_("Invalid date format."), "error")
                target_object_id = request.form.get("target_object_id")

                # Redirect back to the object's page where the form was
                if target_object_id:
                    return redirect(url_for('core.graph_dashboard', object_name=target_object_id))
                else:
                    # Fallback if no object was specified
                    return redirect(url_for('journal.journal_list_view'))
            # --- END FIX ---

            # --- Handle Project Creation/Selection (This logic is still valid) ---
            project_id_for_session = None
            project_selection = request.form.get("project_selection")
            new_project_name = request.form.get("new_project_name", "").strip()

            if project_selection == "new_project" and new_project_name:
                new_project = Project(id=uuid.uuid4().hex, user_id=user.id, name=new_project_name)
                db.add(new_project)
                db.flush()
                project_id_for_session = new_project.id
                target_object_id = request.form.get("target_object_id", "").strip()
                if target_object_id:
                    new_project.target_object_name = target_object_id
            elif project_selection and project_selection not in ["standalone", "new_project"]:
                project_id_for_session = project_selection

            # --- NEW: Get Rig Snapshot Specs and Component Names ---
            rig_id_str = request.form.get("rig_id_snapshot")
            rig_id_snap, rig_name_snap, efl_snap, fr_snap, scale_snap, fov_w_snap, fov_h_snap = None, None, None, None, None, None, None
            tel_name_snap, reducer_name_snap, camera_name_snap = None, None, None

            if rig_id_str:
                try:
                    rig_id = int(rig_id_str)
                    # Use selectinload to ensure components are fetched efficiently
                    rig = db.query(Rig).options(
                        selectinload(Rig.telescope), selectinload(Rig.camera), selectinload(Rig.reducer_extender)
                    ).filter_by(id=rig_id, user_id=user.id).one_or_none()

                    if rig:
                        rig_id_snap = rig.id # <-- SAVE THE ID
                        rig_name_snap = rig.rig_name
                        efl_snap, fr_snap, scale_snap, fov_w_snap = _compute_rig_metrics_from_components(
                            rig.telescope, rig.camera, rig.reducer_extender
                        )
                        if rig.camera and rig.camera.sensor_height_mm and efl_snap:
                            fov_h_snap = (degrees(2 * atan((rig.camera.sensor_height_mm / 2.0) / efl_snap)) * 60.0)

                        # --- NEW: Save Component Names ---
                        tel_name_snap = rig.telescope.name if rig.telescope else None
                        reducer_name_snap = rig.reducer_extender.name if rig.reducer_extender else None
                        camera_name_snap = rig.camera.name if rig.camera else None
                        # --- END NEW ---
                except (ValueError, TypeError):
                    pass # rig_id_str was invalid (e.g., "")

            # --- Create New Session Object (This logic is still valid) ---
            new_session = JournalSession(
                user_id=user.id,
                project_id=project_id_for_session,
                date_utc=parsed_date_utc,  # <-- Use the validated date
                object_name=request.form.get("target_object_id", "").strip(),
                # Fix: Capture location name from form
                location_name=request.form.get("location_name"),
                notes=request.form.get("general_notes_problems_learnings"),
                seeing_observed_fwhm=safe_float(request.form.get("seeing_observed_fwhm")),
                sky_sqm_observed=safe_float(request.form.get("sky_sqm_observed")),
                moon_illumination_session=safe_int(request.form.get("moon_illumination_session")),
                moon_angular_separation_session=safe_float(request.form.get("moon_angular_separation_session")),
                telescope_setup_notes=request.form.get("telescope_setup_notes", "").strip(),
                guiding_rms_avg_arcsec=safe_float(request.form.get("guiding_rms_avg_arcsec")),
                exposure_time_per_sub_sec=safe_int(request.form.get("exposure_time_per_sub_sec")),
                number_of_subs_light=safe_int(request.form.get("number_of_subs_light")),
                filter_used_session=request.form.get("filter_used_session", "").strip(),
                gain_setting=safe_int(request.form.get("gain_setting")),
                offset_setting=safe_int(request.form.get("offset_setting")),
                session_rating_subjective=safe_int(request.form.get("session_rating_subjective")),
                filter_L_subs=safe_int(request.form.get("filter_L_subs")),
                filter_L_exposure_sec=safe_int(request.form.get("filter_L_exposure_sec")),
                filter_R_subs=safe_int(request.form.get("filter_R_subs")),
                filter_R_exposure_sec=safe_int(request.form.get("filter_R_exposure_sec")),
                filter_G_subs=safe_int(request.form.get("filter_G_subs")),
                filter_G_exposure_sec=safe_int(request.form.get("filter_G_exposure_sec")),
                filter_B_subs=safe_int(request.form.get("filter_B_subs")),
                filter_B_exposure_sec=safe_int(request.form.get("filter_B_exposure_sec")),
                filter_Ha_subs=safe_int(request.form.get("filter_Ha_subs")),
                filter_Ha_exposure_sec=safe_int(request.form.get("filter_Ha_exposure_sec")),
                filter_OIII_subs=safe_int(request.form.get("filter_OIII_subs")),
                filter_OIII_exposure_sec=safe_int(request.form.get("filter_OIII_exposure_sec")),
                filter_SII_subs=safe_int(request.form.get("filter_SII_subs")),
                filter_SII_exposure_sec=safe_int(request.form.get("filter_SII_exposure_sec")),
                external_id=generate_session_id(),
                weather_notes=request.form.get("weather_notes", "").strip() or None,
                guiding_equipment=request.form.get("guiding_equipment", "").strip() or None,
                dither_details=request.form.get("dither_details", "").strip() or None,
                dither_pixels=safe_int(request.form.get("dither_pixels")),
                dither_every_n=safe_int(request.form.get("dither_every_n")),
                dither_notes=request.form.get("dither_notes", "").strip() or None,
                acquisition_software=request.form.get("acquisition_software", "").strip() or None,
                camera_temp_setpoint_c=safe_float(request.form.get("camera_temp_setpoint_c")),
                camera_temp_actual_avg_c=safe_float(request.form.get("camera_temp_actual_avg_c")),
                binning_session=request.form.get("binning_session", "").strip() or None,
                darks_strategy=request.form.get("darks_strategy", "").strip() or None,
                flats_strategy=request.form.get("flats_strategy", "").strip() or None,
                bias_darkflats_strategy=request.form.get("bias_darkflats_strategy", "").strip() or None,
                transparency_observed_scale=request.form.get("transparency_observed_scale", "").strip() or None,

                # --- Rig Snapshot Fields ---
                rig_id_snapshot=rig_id_snap,
                rig_name_snapshot=rig_name_snap,
                rig_efl_snapshot=efl_snap,
                rig_fr_snapshot=fr_snap,
                rig_scale_snapshot=scale_snap,
                rig_fov_w_snapshot=fov_w_snap,
                rig_fov_h_snapshot=fov_h_snap,

                # --- NEW COMPONENT NAME SNAPSHOTS ---
                telescope_name_snapshot=tel_name_snap,
                reducer_name_snapshot=reducer_name_snap,
                camera_name_snapshot=camera_name_snap
            )

            # --- Custom filter data (user-defined filters stored as JSON) ---
            custom_data = {}
            for cf in db.query(UserCustomFilter).filter_by(user_id=user.id).all():
                subs = request.form.get(f'filter_{cf.filter_key}_subs')
                exp = request.form.get(f'filter_{cf.filter_key}_exposure_sec')
                if subs or exp:
                    custom_data[f'filter_{cf.filter_key}_subs'] = int(subs) if subs else None
                    custom_data[f'filter_{cf.filter_key}_exposure_sec'] = int(exp) if exp else None
            new_session.custom_filter_data = json.dumps(custom_data) if custom_data else None

            # --- Total exposure calculation (light frames + fixed + custom filters) ---
            FIXED_FILTER_KEYS = ['L', 'R', 'G', 'B', 'Ha', 'OIII', 'SII']
            total_seconds = 0

            # Include light frames (number_of_subs_light × exposure_time_per_sub_sec)
            if new_session.number_of_subs_light and new_session.exposure_time_per_sub_sec:
                total_seconds += int(new_session.number_of_subs_light) * int(new_session.exposure_time_per_sub_sec)

            for fk in FIXED_FILTER_KEYS:
                subs = getattr(new_session, f'filter_{fk}_subs', None) or 0
                exp = getattr(new_session, f'filter_{fk}_exposure_sec', None) or 0
                total_seconds += int(subs) * int(exp)

            if new_session.custom_filter_data:
                custom_data_parsed = json.loads(new_session.custom_filter_data)
                for cf in db.query(UserCustomFilter).filter_by(user_id=user.id).all():
                    subs = custom_data_parsed.get(f'filter_{cf.filter_key}_subs') or 0
                    exp = custom_data_parsed.get(f'filter_{cf.filter_key}_exposure_sec') or 0
                    total_seconds += int(subs) * int(exp)

            new_session.calculated_integration_time_minutes = round(total_seconds / 60.0,
                                                                    1) if total_seconds > 0 else None

            db.add(new_session)
            db.flush()

            if 'session_image' in request.files:
                file = request.files['session_image']
                if file and file.filename != '' and allowed_file(file.filename):
                    file_extension = file.filename.rsplit('.', 1)[1].lower()
                    new_filename = f"{new_session.id}.{file_extension}"
                    user_upload_dir = os.path.join(UPLOAD_FOLDER, username)
                    os.makedirs(user_upload_dir, exist_ok=True)
                    file.save(os.path.join(user_upload_dir, new_filename))
                    new_session.session_image_file = new_filename

            # --- Log file uploads (stored on filesystem, path in DB) ---
            # Read content first, we'll save after commit when we have the session ID
            asiair_content = None
            asiair_filename = None
            phd2_content = None
            phd2_filename = None
            nina_content = None
            nina_filename = None

            if 'asiair_log' in request.files:
                log_file = request.files['asiair_log']
                if log_file and log_file.filename != '':
                    asiair_content = log_file.read().decode('utf-8', errors='ignore')
                    asiair_filename = log_file.filename

            if 'phd2_log' in request.files:
                log_file = request.files['phd2_log']
                if log_file and log_file.filename != '':
                    phd2_content = log_file.read().decode('utf-8', errors='ignore')
                    phd2_filename = log_file.filename

            if 'nina_log' in request.files:
                log_file = request.files['nina_log']
                if log_file and log_file.filename != '':
                    nina_content = log_file.read().decode('utf-8', errors='ignore')
                    nina_filename = log_file.filename

            db.commit()  # Commit to get session ID

            # Now save logs to filesystem with session ID
            if asiair_content:
                path = save_log_to_filesystem(new_session.id, 'asiair', asiair_content, asiair_filename)
                new_session.asiair_log_content = path
            if phd2_content:
                path = save_log_to_filesystem(new_session.id, 'phd2', phd2_content, phd2_filename)
                new_session.phd2_log_content = path
            if nina_content:
                path = save_log_to_filesystem(new_session.id, 'nina', nina_content, nina_filename)
                new_session.nina_log_content = path
            if asiair_content or phd2_content or nina_content:
                db.commit()

            # --- Handle action field (save_draft vs save_close) ---
            action = request.form.get("form_action")
            if action == "save_draft":
                # Save as draft, return JSON without redirect
                new_session.draft = True
                db.commit()
                return jsonify({"status": "ok", "session_id": new_session.id})
            else:
                # save_close or no action (legacy): save as non-draft and redirect
                new_session.draft = False
                db.commit()
                flash(_("New journal entry added successfully!"), "success")
                record_event('journal_session_created')
                # Fix: Pass the session's location to the redirect so the dashboard loads the correct context
                return redirect(url_for('core.graph_dashboard', object_name=new_session.object_name, session_id=new_session.id,
                                        location=new_session.location_name))
        except Exception as e:
            db.rollback()
            raise e

    # --- GET Request Logic ---
    target_object = request.args.get('target')
    if target_object:
        # If a target is specified, go to that object's dashboard
        return redirect(url_for('core.graph_dashboard', object_name=target_object))
    else:
        # If no target, go to the main journal list
        flash(_("To add a new session, please select an object first."), "info")
        return redirect(url_for('journal.journal_list_view'))


@journal_bp.route('/journal/edit/<int:session_id>', methods=['GET', 'POST'])
@login_required
def journal_edit(session_id):
    load_full_astro_context()
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    user = db.query(DbUser).filter_by(username=username).one_or_none()
    session_to_edit = db.query(JournalSession).filter_by(id=session_id, user_id=user.id).one_or_none()

    if not session_to_edit:
        flash(_("Journal entry not found or you do not have permission to edit it."), "error")
        return redirect(url_for('core.index'))

    if request.method == 'POST':
        # --- START FIX: Validate the date ---
        session_date_str = request.form.get("session_date")
        try:
            parsed_date_utc = datetime.strptime(session_date_str, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            flash(_("Invalid date format."), "error")

            # Redirect back to the graph view for this session
            return redirect(url_for('core.graph_dashboard',
                                    object_name=session_to_edit.object_name,
                                    session_id=session_id))
        # --- END FIX ---

        # --- NEW: Get Rig Snapshot Specs and Component Names ---
        rig_id_str = request.form.get("rig_id_snapshot")
        rig_id_snap, rig_name_snap, efl_snap, fr_snap, scale_snap, fov_w_snap, fov_h_snap = None, None, None, None, None, None, None
        tel_name_snap, reducer_name_snap, camera_name_snap = None, None, None  # Initialize new snapshots

        if rig_id_str:
            try:
                rig_id = int(rig_id_str)
                rig = db.query(Rig).options(
                    selectinload(Rig.telescope), selectinload(Rig.camera), selectinload(Rig.reducer_extender)
                ).filter_by(id=rig_id, user_id=user.id).one_or_none()

                if rig:
                    rig_id_snap = rig.id  # <-- SAVE THE ID
                    rig_name_snap = rig.rig_name
                    efl_snap, fr_snap, scale_snap, fov_w_snap = _compute_rig_metrics_from_components(
                        rig.telescope, rig.camera, rig.reducer_extender
                    )
                    if rig.camera and rig.camera.sensor_height_mm and efl_snap:
                        fov_h_snap = (degrees(2 * atan((rig.camera.sensor_height_mm / 2.0) / efl_snap)) * 60.0)

                    # --- GET AND SAVE COMPONENT NAMES ---
                    tel_name_snap = rig.telescope.name if rig.telescope else None
                    reducer_name_snap = rig.reducer_extender.name if rig.reducer_extender else None
                    camera_name_snap = rig.camera.name if rig.camera else None
                    # --- END COMPONENT NAMES ---
            except (ValueError, TypeError):
                pass

        # --- Update ALL fields from the form ---
        session_to_edit.date_utc = parsed_date_utc
        session_to_edit.object_name = request.form.get("target_object_id", "").strip()
        # Fix: Update location name from form
        session_to_edit.location_name = request.form.get("location_name")
        session_to_edit.notes = request.form.get("general_notes_problems_learnings")

        form = request.form
        session_to_edit.seeing_observed_fwhm = safe_float(form.get("seeing_observed_fwhm"))
        session_to_edit.sky_sqm_observed = safe_float(form.get("sky_sqm_observed"))
        session_to_edit.moon_illumination_session = safe_int(form.get("moon_illumination_session"))
        session_to_edit.moon_angular_separation_session = safe_float(form.get("moon_angular_separation_session"))
        session_to_edit.telescope_setup_notes = form.get("telescope_setup_notes", "").strip()
        session_to_edit.guiding_rms_avg_arcsec = safe_float(form.get("guiding_rms_avg_arcsec"))
        session_to_edit.exposure_time_per_sub_sec = safe_int(form.get("exposure_time_per_sub_sec"))
        session_to_edit.number_of_subs_light = safe_int(form.get("number_of_subs_light"))
        session_to_edit.filter_used_session = form.get("filter_used_session", "").strip()
        session_to_edit.gain_setting = safe_int(form.get("gain_setting"))
        session_to_edit.offset_setting = safe_int(form.get("offset_setting"))
        session_to_edit.session_rating_subjective = safe_int(form.get("session_rating_subjective"))
        session_to_edit.filter_L_subs = safe_int(form.get("filter_L_subs"))
        session_to_edit.filter_L_exposure_sec = safe_int(form.get("filter_L_exposure_sec"))
        session_to_edit.filter_R_subs = safe_int(form.get("filter_R_subs"))
        session_to_edit.filter_R_exposure_sec = safe_int(form.get("filter_R_exposure_sec"))
        session_to_edit.filter_G_subs = safe_int(form.get("filter_G_subs"))
        session_to_edit.filter_G_exposure_sec = safe_int(form.get("filter_G_exposure_sec"))
        session_to_edit.filter_B_subs = safe_int(form.get("filter_B_subs"))
        session_to_edit.filter_B_exposure_sec = safe_int(form.get("filter_B_exposure_sec"))
        session_to_edit.filter_Ha_subs = safe_int(form.get("filter_Ha_subs"))
        session_to_edit.filter_Ha_exposure_sec = safe_int(form.get("filter_Ha_exposure_sec"))
        session_to_edit.filter_OIII_subs = safe_int(form.get("filter_OIII_subs"))
        session_to_edit.filter_OIII_exposure_sec = safe_int(form.get("filter_OIII_exposure_sec"))
        session_to_edit.filter_SII_subs = safe_int(form.get("filter_SII_subs"))
        session_to_edit.filter_SII_exposure_sec = safe_int(form.get("filter_SII_exposure_sec"))
        session_to_edit.weather_notes = form.get("weather_notes", "").strip() or None
        session_to_edit.guiding_equipment = form.get("guiding_equipment", "").strip() or None
        session_to_edit.dither_details = form.get("dither_details", "").strip() or None
        session_to_edit.dither_pixels = safe_int(form.get("dither_pixels"))
        session_to_edit.dither_every_n = safe_int(form.get("dither_every_n"))
        session_to_edit.dither_notes = form.get("dither_notes", "").strip() or None
        session_to_edit.acquisition_software = form.get("acquisition_software", "").strip() or None
        session_to_edit.camera_temp_setpoint_c = safe_float(form.get("camera_temp_setpoint_c"))
        session_to_edit.camera_temp_actual_avg_c = safe_float(form.get("camera_temp_actual_avg_c"))
        session_to_edit.binning_session = form.get("binning_session", "").strip() or None
        session_to_edit.darks_strategy = form.get("darks_strategy", "").strip() or None
        session_to_edit.flats_strategy = form.get("flats_strategy", "").strip() or None
        session_to_edit.bias_darkflats_strategy = form.get("bias_darkflats_strategy", "").strip() or None
        session_to_edit.transparency_observed_scale = form.get("transparency_observed_scale", "").strip() or None

        # --- START: Update Rig Snapshot Fields (Crucial Assignment) ---
        session_to_edit.rig_id_snapshot = rig_id_snap
        session_to_edit.rig_name_snapshot = rig_name_snap
        session_to_edit.rig_efl_snapshot = efl_snap
        session_to_edit.rig_fr_snapshot = fr_snap
        session_to_edit.rig_scale_snapshot = scale_snap
        session_to_edit.rig_fov_w_snapshot = fov_w_snap
        session_to_edit.rig_fov_h_snapshot = fov_h_snap

        # --- ASSIGN NEW COMPONENT NAME SNAPSHOTS ---
        session_to_edit.telescope_name_snapshot = tel_name_snap
        session_to_edit.reducer_name_snapshot = reducer_name_snap
        session_to_edit.camera_name_snapshot = camera_name_snap
        # --- END: Update Rig Snapshot Fields ---

        # --- Custom filter data (user-defined filters stored as JSON) ---
        custom_data = {}
        for cf in db.query(UserCustomFilter).filter_by(user_id=user.id).all():
            subs = request.form.get(f'filter_{cf.filter_key}_subs')
            exp = request.form.get(f'filter_{cf.filter_key}_exposure_sec')
            if subs or exp:
                custom_data[f'filter_{cf.filter_key}_subs'] = int(subs) if subs else None
                custom_data[f'filter_{cf.filter_key}_exposure_sec'] = int(exp) if exp else None
        session_to_edit.custom_filter_data = json.dumps(custom_data) if custom_data else None

        # Project logic
        project_id_for_session = None
        project_selection = request.form.get("project_selection")
        new_project_name = request.form.get("new_project_name", "").strip()

        # The object being viewed/edited (use this consistently)
        target_object_id = session_to_edit.object_name

        if project_selection == "new_project" and new_project_name:
            new_project = Project(id=uuid.uuid4().hex, user_id=user.id, name=new_project_name)
            db.add(new_project)
            db.flush()
            project_id_for_session = new_project.id

            # Link the NEW project to the object
            if target_object_id:
                new_project.target_object_name = target_object_id

        elif project_selection and project_selection not in ["standalone", "new_project"]:
            project_id_for_session = project_selection

            # Link the EXISTING project to the object
            project_to_link = db.query(Project).filter_by(id=project_id_for_session, user_id=user.id).one_or_none()
            if project_to_link and target_object_id:
                project_to_link.target_object_name = target_object_id

        session_to_edit.project_id = project_id_for_session

        # --- Total exposure calculation (fixed + custom filters) ---
        FIXED_FILTER_KEYS = ['L', 'R', 'G', 'B', 'Ha', 'OIII', 'SII']
        total_seconds = 0

        # Main light subs
        total_seconds += (session_to_edit.number_of_subs_light or 0) * (session_to_edit.exposure_time_per_sub_sec or 0)

        for fk in FIXED_FILTER_KEYS:
            subs = getattr(session_to_edit, f'filter_{fk}_subs', None) or 0
            exp = getattr(session_to_edit, f'filter_{fk}_exposure_sec', None) or 0
            total_seconds += int(subs) * int(exp)

        if session_to_edit.custom_filter_data:
            custom_data_parsed = json.loads(session_to_edit.custom_filter_data)
            for cf in db.query(UserCustomFilter).filter_by(user_id=user.id).all():
                subs = custom_data_parsed.get(f'filter_{cf.filter_key}_subs') or 0
                exp = custom_data_parsed.get(f'filter_{cf.filter_key}_exposure_sec') or 0
                total_seconds += int(subs) * int(exp)

        session_to_edit.calculated_integration_time_minutes = round(total_seconds / 60.0,
                                                                    1) if total_seconds > 0 else None
        # File Handling (Delete Image)
        if request.form.get('delete_session_image') == '1':
            old_image = session_to_edit.session_image_file
            if old_image:
                user_upload_dir = os.path.join(UPLOAD_FOLDER, username)
                old_image_path = os.path.join(user_upload_dir, old_image)
                if os.path.exists(old_image_path):
                    try:
                        os.remove(old_image_path)
                    except Exception as e:
                        print(f"Error deleting file: {e}")
                session_to_edit.session_image_file = None

        # File Handling (New Image Upload)
        if 'session_image' in request.files:
            file = request.files['session_image']
            if file and file.filename != '' and allowed_file(file.filename):
                file_extension = file.filename.rsplit('.', 1)[1].lower()
                new_filename = f"{session_to_edit.id}.{file_extension}"
                user_upload_dir = os.path.join(UPLOAD_FOLDER, username)
                os.makedirs(user_upload_dir, exist_ok=True)
                file.save(os.path.join(user_upload_dir, new_filename))
                session_to_edit.session_image_file = new_filename

        # --- Log file handling (stored as TEXT in DB) ---
        # Track if we need to invalidate the analysis cache
        invalidate_cache = False

        # Log file uploads (stored on filesystem, path in DB)
        if 'asiair_log' in request.files:
            log_file = request.files['asiair_log']
            if log_file and log_file.filename != '':
                content = log_file.read().decode('utf-8', errors='ignore')
                path = save_log_to_filesystem(session_to_edit.id, 'asiair', content, log_file.filename)
                session_to_edit.asiair_log_content = path
                invalidate_cache = True

        if 'phd2_log' in request.files:
            log_file = request.files['phd2_log']
            if log_file and log_file.filename != '':
                content = log_file.read().decode('utf-8', errors='ignore')
                path = save_log_to_filesystem(session_to_edit.id, 'phd2', content, log_file.filename)
                session_to_edit.phd2_log_content = path
                invalidate_cache = True

        # NINA log upload with validation
        if 'nina_log' in request.files:
            log_file = request.files['nina_log']
            if log_file and log_file.filename != '':
                # Validate file extension
                if not log_file.filename.lower().endswith('.log'):
                    flash(_("NINA log file must be a .log file."), "error")
                    return redirect(
                        url_for('core.graph_dashboard', object_name=session_to_edit.object_name,
                                session_id=session_id, location=session_to_edit.location_name))

                # Validate file size (10 MB max)
                log_file.seek(0, os.SEEK_END)
                file_size = log_file.tell()
                log_file.seek(0)  # Reset to beginning for reading

                MAX_SIZE = 10 * 1024 * 1024  # 10 MB
                if file_size > MAX_SIZE:
                    flash(_("NINA log file is too large. Maximum size is 10 MB."), "error")
                    return redirect(
                        url_for('core.graph_dashboard', object_name=session_to_edit.object_name,
                                session_id=session_id, location=session_to_edit.location_name))

                content = log_file.read().decode('utf-8', errors='ignore')
                path = save_log_to_filesystem(session_to_edit.id, 'nina', content, log_file.filename)
                session_to_edit.nina_log_content = path
                invalidate_cache = True
                flash(_("NINA log imported successfully."), "success")

        # Log deletion via checkbox
        if request.form.get('delete_asiair_log') == '1':
            session_to_edit.asiair_log_content = None
            invalidate_cache = True

        if request.form.get('delete_phd2_log') == '1':
            session_to_edit.phd2_log_content = None
            invalidate_cache = True

        if request.form.get('delete_nina_log') == '1':
            session_to_edit.nina_log_content = None
            invalidate_cache = True

        # Invalidate analysis cache if logs changed
        if invalidate_cache:
            session_to_edit.log_analysis_cache = None

        # --- Handle action field (save_draft vs save_close) ---
        action = request.form.get("form_action")
        if action == "save_draft":
            # Save as draft, return JSON without redirect
            session_to_edit.draft = True
            db.commit()
            return jsonify({"status": "ok", "session_id": session_id})
        else:
            # save_close or no action (legacy): save as non-draft and redirect
            session_to_edit.draft = False
            db.commit()
            if not request.files.get('nina_log') or request.files['nina_log'].filename == '':
                flash(_("Journal entry updated successfully!"), "success")
            record_event('journal_session_edited')
            # Fix: Pass the session's location to the redirect so the dashboard loads the correct context
            return redirect(
                url_for('core.graph_dashboard', object_name=session_to_edit.object_name, session_id=session_id,
                        location=session_to_edit.location_name))

    # --- GET Request Logic ---
    if not session_to_edit.object_name:
        flash(_("Cannot edit session: associated object name is missing."), "error")
        return redirect(url_for('journal.journal_list_view'))

    return redirect(url_for('core.graph_dashboard',
                            object_name=session_to_edit.object_name,
                            session_id=session_id))


@journal_bp.route('/journal/add_project', methods=['POST'])
@login_required
def add_project_from_journal():
    from nova import trigger_outlook_update_for_user  # Lazy import
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    # Capture location to persist view state on redirect
    current_location = request.form.get('current_location')

    try:
        user = db.query(DbUser).filter_by(username=username).one()

        name = request.form.get('name')
        target_object_id = request.form.get('target_object_id')
        status = request.form.get('status', 'In Progress')
        goals = request.form.get('goals')

        if not name:
            flash(_("Project name is required."), "error")
            return redirect(
                url_for('core.graph_dashboard', object_name=target_object_id, tab='journal', location=current_location))

        existing = db.query(Project).filter_by(user_id=user.id, name=name).first()
        if existing:
            flash(_("A project named '%(project_name)s' already exists.", project_name=name), "error")
            return redirect(
                url_for('core.graph_dashboard', object_name=target_object_id, tab='journal', location=current_location))

        new_project = Project(
            id=uuid.uuid4().hex,
            user_id=user.id,
            name=name,
            target_object_name=target_object_id,
            status=status,
            goals=goals
        )
        db.add(new_project)

        # Auto-activate object if project is In Progress
        should_trigger_outlook = False
        if target_object_id and status == 'In Progress':
            obj = db.query(AstroObject).filter_by(user_id=user.id, object_name=target_object_id).first()
            if obj and not obj.active_project:
                obj.active_project = True
                should_trigger_outlook = True

        db.commit()

        if should_trigger_outlook:
            trigger_outlook_update_for_user(username)

        flash(_("Project '%(project_name)s' created successfully.", project_name=name), "success")

        # Build redirect args explicitly to ensure clean URL construction
        redirect_args = {
            'object_name': target_object_id,
            'tab': 'journal',
            'project_id': new_project.id
        }
        if current_location:
            redirect_args['location'] = current_location

        return redirect(url_for('core.graph_dashboard', **redirect_args))

    except Exception as e:
        db.rollback()
        print(f"Error creating project in journal: {e}")  # Log error for debugging
        flash(_("Error creating project: %(error)s", error=e), "error")
        return redirect(url_for('core.graph_dashboard', object_name=request.form.get('target_object_id'), tab='journal',
                                location=current_location))


@journal_bp.route('/journal/duplicate/<int:session_id>', methods=['POST'])
@login_required
def journal_duplicate(session_id):
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        user = db.query(DbUser).filter_by(username=username).one()
        source_session = db.query(JournalSession).filter_by(id=session_id, user_id=user.id).one_or_none()

        if not source_session:
            flash(_("Session to duplicate not found."), "error")
            return redirect(url_for('core.index'))

        # Create new instance
        new_session = JournalSession()

        # Fields to EXCLUDE from copy
        exclude_cols = {'id', 'external_id', 'session_image_file', '_sa_instance_state'}

        # Dynamically copy all other columns
        for col in source_session.__table__.columns:
            if col.name not in exclude_cols:
                setattr(new_session, col.name, getattr(source_session, col.name))

        # Set new unique values
        new_session.external_id = uuid.uuid4().hex
        new_session.date_utc = datetime.now().date()  # Default to today for the new session

        # Append note to indicate copy
        # if new_session.notes:
        #     new_session.notes += "<br><p><em>(Duplicated Session)</em></p>"

        db.add(new_session)
        db.commit()

        flash(_("Session duplicated successfully."), "success")

        # Redirect to Graph Dashboard with 'edit=true' to open the form immediately
        return redirect(url_for('core.graph_dashboard',
                                object_name=new_session.object_name,
                                session_id=new_session.id,
                                location=new_session.location_name,
                                edit='true'))

    except Exception as e:
        db.rollback()
        flash(_("Error duplicating session: %(error)s", error=e), "error")
        return redirect(url_for('core.index'))


@journal_bp.route('/journal/delete/<int:session_id>', methods=['POST'])
@login_required
def journal_delete(session_id):
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    user = db.query(DbUser).filter_by(username=username).one()
    session_to_delete = db.query(JournalSession).filter_by(id=session_id, user_id=user.id).one_or_none()

    if session_to_delete:
        object_name_redirect = session_to_delete.object_name
        # Delete associated image file
        if session_to_delete.session_image_file:
            image_path = os.path.join(UPLOAD_FOLDER, username, session_to_delete.session_image_file)
            if os.path.exists(image_path):
                os.remove(image_path)

        db.delete(session_to_delete)
        db.commit()
        flash(_("Journal entry deleted successfully."), "success")
        if object_name_redirect:
            return redirect(url_for('core.graph_dashboard', object_name=object_name_redirect))
        else:
            return redirect(url_for('core.index'))
    else:
        flash(_("Journal entry not found or you do not have permission to delete it."), "error")
        return redirect(url_for('core.index'))


@journal_bp.route('/journal/report_page/<int:session_id>')
@login_required
def show_journal_report_page(session_id):
    """
    Renders the HTML version of the report page.
    """
    from nova.log_parser import parse_asiair_log, parse_phd2_log

    db = get_db()
    try:
        # --- 1. Get Session Data ---
        session = db.query(JournalSession).filter_by(id=session_id, user_id=g.db_user.id).one_or_none()
        if not session:
            flash(_("Session not found."), "error")
            return redirect(url_for('core.index'))

        session_dict = {c.name: getattr(session, c.name) for c in session.__table__.columns}

        project = None
        project_name = "Standalone Session"

        if session.project_id:
            project = db.query(Project).filter_by(id=session.project_id).one_or_none()
            if project:
                project_name = project.name

        # --- 2. Get Related Data ---
        obj_record = db.query(AstroObject).filter_by(user_id=g.db_user.id,
                                                     object_name=session.object_name).one_or_none()

        if obj_record:
            object_details = {
                'Object': obj_record.object_name,
                'Common Name': obj_record.common_name or obj_record.object_name,
                'Type': obj_record.type or 'Deep Sky Object',
                'Constellation': obj_record.constellation or 'N/A'
            }
        else:
            object_details = get_ra_dec(session.object_name) or {'Common Name': session.object_name,
                                                                 'Object': session.object_name}

        # --- 3. Prepare Template Variables ---
        rating = session_dict.get('session_rating_subjective') or 0
        rating_stars = "★" * rating + "☆" * (5 - rating)

        integ_min = session_dict.get('calculated_integration_time_minutes') or 0
        integ_str = f"{integ_min // 60}h {integ_min % 60:.0f}m" if integ_min > 0 else "N/A"

        image_url = None
        image_source_label = "Session Image"

        username = "default" if SINGLE_USER_MODE else current_user.username

        # Try Session Image
        if session_dict.get('session_image_file'):
            image_url = url_for('core.get_uploaded_image', username=username, filename=session_dict['session_image_file'],
                                _external=True)
            image_source_label = "Session Result / Preview"

        # Fallback to Project Image
        elif project and project.final_image_file:
            image_url = url_for('core.get_uploaded_image', username=username, filename=project.final_image_file,
                                _external=True)
            image_source_label = "Project Context (Final Image)"

        # Logo
        logo_url = url_for('static', filename='nova-icon-transparent.png', _external=True)

        # Sanitize notes
        raw_journal_notes = session_dict.get('notes') or ""
        if not raw_journal_notes.strip().startswith(("<p>", "<div>", "<ul>", "<ol>", "<figure>", "<blockquote>", "<h1>", "<h2>", "<h3>", "<h4>", "<h5>", "<h6>")):
            escaped_text = bleach.clean(raw_journal_notes, tags=[], strip=True)
            sanitized_notes = escaped_text.replace("\n", "<br>")
        else:
            SAFE_TAGS = ['p', 'strong', 'em', 'ul', 'ol', 'li', 'br', 'div', 'img', 'a', 'figure', 'figcaption', 'span']
            SAFE_ATTRS = {'img': ['src', 'alt', 'width', 'height', 'style'], 'a': ['href'], '*': ['style', 'class']}
            SAFE_CSS = ['text-align', 'width', 'height', 'max-width', 'float', 'margin', 'margin-left', 'margin-right']
            css_sanitizer = CSSSanitizer(allowed_css_properties=SAFE_CSS)
            sanitized_notes = bleach.clean(raw_journal_notes, tags=SAFE_TAGS, attributes=SAFE_ATTRS,
                                           css_sanitizer=css_sanitizer)
        session_dict['notes'] = sanitized_notes

        # Add dither_display for template rendering
        session_dict['dither_display'] = dither_display(session)

        # --- 4. Parse Log Files and Generate Charts ---
        log_analysis = {'has_logs': False, 'asiair': None, 'phd2': None}
        chart_images = {
            'guiding_rms': None,
            'guiding_scatter': None,
            'dither_settle': None,
            'af_vcurve': None,
            'af_drift': None,
            'autocenter': None
        }

        try:
            # Read ASIAIR log
            asiair_content = read_log_content(session_dict.get('asiair_log_content'))
            asiair_data = parse_asiair_log(asiair_content) if asiair_content else None

            # Read PHD2 log
            phd2_content = read_log_content(session_dict.get('phd2_log_content'))
            phd2_data = parse_phd2_log(phd2_content) if phd2_content else None

            # Check if we have any log data
            has_logs = bool(asiair_data and asiair_data.get('exposures')) or bool(phd2_data and phd2_data.get('frames'))

            if has_logs:
                log_analysis = {
                    'has_logs': True,
                    'asiair': asiair_data,
                    'phd2': phd2_data
                }
                # Generate all charts
                chart_images = generate_session_charts(log_analysis)
        except Exception as log_error:
            print(f"[report] Error parsing logs for session {session_id}: {log_error}")
            traceback.print_exc()

        record_event('pdf_report_generated')
        return render_template(
            'journal_report.html',
            session=session_dict,
            object_details=object_details,
            project_name=project_name,
            rating_stars=rating_stars,
            integ_str=integ_str,
            image_url=image_url,
            image_source_label=image_source_label,
            logo_url=logo_url,
            today_date=datetime.now().strftime('%d.%m.%Y'),
            log_analysis=log_analysis,
            chart_images=chart_images
        )

    except Exception as e:
        print(f"Error rendering report page: {e}")
        traceback.print_exc()
        return f"Error generating report: {e}", 500


@journal_bp.route('/journal/add_for_target/<path:object_name>', methods=['GET', 'POST'])
@login_required
def journal_add_for_target(object_name):
    if request.method == 'POST':
        # If the form is submitted, redirect the POST request to the main journal_add function
        # which already contains all the logic to process the form data.
        return redirect(url_for('journal.journal_add'), code=307)

    # For GET requests, the original behavior is maintained.
    return redirect(url_for('journal.journal_add', target=object_name))


@journal_bp.route('/journal/download_csv/<string:item_type>/<string:item_id>')
@login_required
def download_csv(item_type, item_id):
    db = get_db()
    try:
        user_id = g.db_user.id
        sessions_to_export = []
        filename = "export.csv"
        project_framing_clean = ""

        if item_type == 'session':
            session = db.query(JournalSession).filter_by(id=item_id, user_id=user_id).one_or_none()
            if not session:
                flash(_("Session not found."), "error")
                return redirect(url_for('core.index'))
            sessions_to_export = [session]
            filename = f"Session_{session.date_utc}_{session.object_name.replace(' ', '_')}.csv"

        elif item_type == 'project':
            project = db.query(Project).filter_by(id=item_id, user_id=user_id).one_or_none()
            if not project:
                flash(_("Project not found."), "error")
                return redirect(url_for('core.index'))

            project_framing_clean = bleach.clean(project.framing_notes or "", tags=[], strip=True).replace("\n", " | ")

            sessions_to_export = db.query(JournalSession).filter_by(project_id=item_id, user_id=user_id).order_by(
                JournalSession.date_utc.asc()).all()
            filename = f"Project_{project.name.replace(' ', '_')}_Sessions.csv"

        else:
            return "Invalid type", 400

        # Define CSV Columns
        columns = [
            "Date", "Object", "Location", "Integration (min)", "Rating",
            "Telescope", "Camera", "Filter",
            "Seeing", "SQM", "Moon %",
            "Subs", "Exposure (s)", "Gain", "Offset", "Temp (C)",
            "Project ID", "Project Framing Notes", "Notes (Stripped)"
        ]

        # Generate CSV in memory
        si = io.StringIO()
        cw = csv.writer(si)
        cw.writerow(columns)

        for s in sessions_to_export:
            # Strip HTML from notes
            notes_clean = bleach.clean(s.notes or "", tags=[], strip=True).replace("\n", " | ")

            row = [
                s.date_utc, s.object_name, s.location_name, s.calculated_integration_time_minutes,
                s.session_rating_subjective,
                s.telescope_name_snapshot, s.camera_name_snapshot, s.filter_used_session,
                s.seeing_observed_fwhm, s.sky_sqm_observed, s.moon_illumination_session,
                s.number_of_subs_light, s.exposure_time_per_sub_sec, s.gain_setting, s.offset_setting,
                s.camera_temp_actual_avg_c,
                s.project_id, project_framing_clean, notes_clean
            ]
            cw.writerow(row)

        output = make_response(si.getvalue())
        output.headers["Content-Disposition"] = f"attachment; filename={filename}"
        output.headers["Content-type"] = "text/csv"
        return output

    except Exception as e:
        print(f"Error generating CSV: {e}")
        return redirect(url_for('core.index'))
