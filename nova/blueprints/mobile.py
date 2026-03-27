"""
Nova DSO Tracker - Mobile Blueprint
----------------------------------
Mobile-optimized routes for the companion app: Up Now dashboard,
location selector, add object, outlook, edit notes, and mosaic view.
"""

# =============================================================================
# Standard Library Imports
# =============================================================================
import math
from datetime import datetime, timedelta

# =============================================================================
# Third-Party Imports
# =============================================================================
import bleach
from flask import (
    Blueprint, render_template, redirect, url_for, flash,
    request, g, session, current_app, jsonify
)

from flask_login import login_required, current_user
from flask_babel import gettext as _
from sqlalchemy.orm import selectinload

# =============================================================================
# Nova Package Imports (no circular import)
# =============================================================================
from nova import SINGLE_USER_MODE  # Import from nova for test patching compatibility
from nova.models import (
    DbUser, AstroObject, SavedFraming, Rig, Location, Project, JournalSession, UserCustomFilter
)
from nova.helpers import (
    get_db, load_full_astro_context, safe_float, safe_int, generate_session_id, _compute_rig_metrics_from_components
)
from nova.analytics import record_event
from uuid import uuid4
from math import degrees, atan
import json
import traceback
import pytz


# =============================================================================
# Blueprint Definition
# =============================================================================
mobile_bp = Blueprint('mobile', __name__)


# =============================================================================
# Mobile Routes
# =============================================================================

@mobile_bp.route('/m/up_now')
@login_required
def mobile_up_now():
    """Renders the mobile 'Up Now' dashboard skeleton (data fetched via API)."""
    load_full_astro_context()
    # Render template immediately with empty data; JS will fetch it.
    return render_template('mobile_up_now.html')

@mobile_bp.route('/m/location')
@login_required
def mobile_location():
    """Renders the mobile location selector."""
    load_full_astro_context()  # <-- ADD THIS LINE
    return render_template('mobile_location.html',
                           locations=g.active_locations,
                           selected_location_name=g.selected_location)

@mobile_bp.route('/m')
@mobile_bp.route('/m/add_object')
@login_required
def mobile_add_object():
    """Renders the mobile 'Add Object' page."""
    load_full_astro_context()  # <-- ADD THIS LINE
    record_event('mobile_view_accessed')
    return render_template('mobile_add_object.html')

@mobile_bp.route('/m/outlook')
@login_required
def mobile_outlook():
    """Renders the mobile 'Outlook' page."""
    load_full_astro_context()  # <-- ADD THIS LINE
    return render_template('mobile_outlook.html')


@mobile_bp.route('/m/edit_notes/<path:object_name>')
@login_required
def mobile_edit_notes(object_name):
    """Renders the mobile 'Edit Notes' page for a specific object."""
    load_full_astro_context()  # Ensures g.db_user is loaded

    # Get the current user
    if SINGLE_USER_MODE:
        username = "default"
    else:
        username = current_user.username

    db = get_db()
    user = db.query(DbUser).filter_by(username=username).one_or_none()
    if not user:
        flash(_("User not found."), "error")
        return redirect(url_for('mobile.mobile_up_now'))

    # Get the specific object
    obj_record = db.query(AstroObject).filter_by(user_id=user.id, object_name=object_name).one_or_none()

    if not obj_record:
        flash(_("Object '%(object_name)s' not found.", object_name=object_name), "error")
        return redirect(url_for('mobile.mobile_up_now'))

    # Handle Trix/HTML conversion for old plain text notes
    raw_project_notes = obj_record.project_name or ""
    if not raw_project_notes.strip().startswith(
            ("<p>", "<div>", "<ul>", "<ol>", "<figure>", "<blockquote>", "<h1>", "<h2>", "<h3>", "<h4>", "<h5>",
             "<h6>")):
        escaped_text = bleach.clean(raw_project_notes, tags=[], strip=True)
        project_notes_for_editor = escaped_text.replace("\n", "<br>")
    else:
        project_notes_for_editor = raw_project_notes

    return render_template(
        'mobile_edit_notes.html',
        object_name=obj_record.object_name,
        common_name=obj_record.common_name,
        project_notes_html=project_notes_for_editor,
        is_project_active=obj_record.active_project
    )


# =============================================================================
# Mobile Helper Functions
# =============================================================================

def _format_ra_csv(ra_deg):
    # Round to nearest second to avoid floating point issues (e.g. 59.999s)
    total_seconds = round((ra_deg / 15.0) * 3600)
    h = int(total_seconds // 3600)
    m = int((total_seconds % 3600) // 60)
    s = int(total_seconds % 60)
    # Telescopius Format: 00hr 00' 00" (Pad with zeros)
    return f"{h:02d}hr {m:02d}' {s:02d}"


def _format_dec_csv(dec_deg):
    sign = '-' if dec_deg < 0 else ''
    dec_abs = abs(dec_deg)
    # Round to nearest second
    total_seconds = round(dec_abs * 3600)
    d = int(total_seconds // 3600)
    m = int((total_seconds % 3600) // 60)
    s = int(total_seconds % 60)
    # Telescopius Format: 41º 53' 27" (Uses ordinal º, no plus sign for positive)
    # FIX: Added padding {d:02d} to match strict 00º format required by importers
    return f'{sign}{d:02d}º {m:02d}\' {s:02d}"'


@mobile_bp.route('/m/framing_coords/<path:object_name>')
@login_required
def mobile_framing_coords(object_name):
    """Mobile page to display formatted framing coordinates."""
    load_full_astro_context()
    db = get_db()

    # Try to get saved framing for this user/object
    framing = db.query(SavedFraming).filter_by(
        user_id=g.db_user.id, object_name=object_name
    ).one_or_none()

    if framing:
        ra_deg = framing.ra
        dec_deg = framing.dec
        has_framing = True
    else:
        # Fall back to request args
        try:
            ra_deg = float(request.args.get('ra', 0))
            dec_deg = float(request.args.get('dec', 0))
        except (TypeError, ValueError):
            ra_deg = 0
            dec_deg = 0
        has_framing = False

    ra_fmt = _format_ra_csv(ra_deg)
    dec_fmt = _format_dec_csv(dec_deg)
    csv_line = f"{object_name},{ra_fmt},{dec_fmt}"

    return render_template('mobile_framing_coords.html',
        object_name=object_name,
        ra_fmt=ra_fmt,
        dec_fmt=dec_fmt,
        csv_line=csv_line,
        has_framing=has_framing,
        back_url=request.referrer or url_for('mobile.mobile_up_now')
    )


@mobile_bp.route('/m/mosaic/<path:object_name>')
@login_required
def mobile_mosaic_view(object_name):
    """Mobile-optimized page to copy the ASIAIR mosaic plan string."""
    db = get_db()
    framing = db.query(SavedFraming).filter_by(
        user_id=g.db_user.id, object_name=object_name
    ).one_or_none()

    if not framing:
        return f"<h3>No saved framing found for {object_name}</h3><p>Please save a framing on the desktop first.</p>"

    # Get Rig Data
    rig = db.get(Rig, framing.rig_id) if framing.rig_id else None
    if not rig or not rig.fov_w_arcmin:
        return "<h3>Error: Rig data missing in saved framing.</h3>"

    # Math Setup
    fov_w_deg = rig.fov_w_arcmin / 60.0
    # If height is missing (older DBs), estimate based on sensor ratio or square
    fov_h_deg = (rig.fov_w_arcmin / 60.0)  # Default square if missing

    # Try to be precise if components exist
    if rig.camera and rig.camera.sensor_height_mm and rig.effective_focal_length:
        fov_h_deg = math.degrees(2 * math.atan((rig.camera.sensor_height_mm / 2.0) / rig.effective_focal_length))

    cols = framing.mosaic_cols or 1
    rows = framing.mosaic_rows or 1
    overlap = (framing.mosaic_overlap or 10.0) / 100.0

    w_step = fov_w_deg * (1 - overlap)
    h_step = fov_h_deg * (1 - overlap)

    # Invert angle for CW rotation to match frontend
    rot_rad = math.radians(-(framing.rotation or 0))
    center_ra_rad = math.radians(framing.ra)
    center_dec_rad = math.radians(framing.dec)

    # Tangent Plane Projection (Matches JS logic)
    cX = math.cos(center_dec_rad) * math.cos(center_ra_rad)
    cY = math.cos(center_dec_rad) * math.sin(center_ra_rad)
    cZ = math.sin(center_dec_rad)
    eX = -math.sin(center_ra_rad);
    eY = math.cos(center_ra_rad);
    eZ = 0
    nX = -math.sin(center_dec_rad) * math.cos(center_ra_rad)
    nY = -math.sin(center_dec_rad) * math.sin(center_ra_rad)
    nZ = math.cos(center_dec_rad)

    output_lines = []
    base_name = object_name.replace(" ", "_")
    pane_count = 1

    # CSV Header - Exact Telescopius Format (9 Columns)
    output_lines.append(
        "Pane, RA, DEC, Position Angle (East), Pane width (arcmins), Pane height (arcmins), Overlap, Row, Column")

    for r in range(rows):
        for c in range(cols):
            cx_off = (c - (cols - 1) / 2.0) * w_step
            cy_off = (r - (rows - 1) / 2.0) * h_step

            # Rotation
            rx = cx_off * math.cos(rot_rad) - cy_off * math.sin(rot_rad)
            ry = cx_off * math.sin(rot_rad) + cy_off * math.cos(rot_rad)

            # De-projection
            dx = math.radians(-rx)  # Negate X for RA
            dy = math.radians(ry)
            rad = math.hypot(dx, dy)

            if rad < 1e-9:
                p_ra = framing.ra
                p_dec = framing.dec
            else:
                sinC = math.sin(rad);
                cosC = math.cos(rad)
                dirX = (dx * eX + dy * nX) / rad
                dirY = (dx * eY + dy * nY) / rad
                dirZ = (dx * eZ + dy * nZ) / rad

                pX = cosC * cX + sinC * dirX
                pY = cosC * cY + sinC * dirY
                pZ = cosC * cZ + sinC * dirZ

                ra_rad_res = math.atan2(pY, pX)
                if ra_rad_res < 0: ra_rad_res += 2 * math.pi
                p_ra = math.degrees(ra_rad_res)
                p_dec = math.degrees(math.asin(pZ))

            # Ensure Rotation is 0-360 positive for ASIAIR
            csv_rot = int(round((framing.rotation or 0) % 360))
            if csv_rot < 0: csv_rot += 360

            # Format Data Columns using J2000 (ASIAIR handles JNow internally)
            p_name = f"{base_name}_P{pane_count}"
            p_ra_str = _format_ra_csv(p_ra)
            p_dec_str = _format_dec_csv(p_dec)
            p_rot = f"{csv_rot:.2f}"

            # Rig Dimensions
            rig_w = f"{rig.fov_w_arcmin:.2f}" if rig and rig.fov_w_arcmin else "0.00"
            rig_h = f"{fov_h_deg * 60:.2f}"

            # Overlap and Grid Index
            p_ov = f"{int(framing.mosaic_overlap)}%"
            p_row = r + 1
            p_col = c + 1

            # Construct CSV Line (with spaces after commas)
            line = f"{p_name}, {p_ra_str}, {p_dec_str}, {p_rot}, {rig_w}, {rig_h}, {p_ov}, {p_row}, {p_col}"
            output_lines.append(line)
            pane_count += 1

    full_text = "\n".join(output_lines)

    # Determine back link based on source
    source = request.args.get('from')
    if source == 'outlook':
        back_url = url_for('mobile.mobile_outlook')
    else:
        # Default to up_now for direct access or 'up_now' source
        back_url = url_for('mobile.mobile_up_now')

    return render_template('mobile_mosaic_copy.html',
                           object_name=object_name,
                           mosaic_text=full_text,
                           info=f"{cols}x{rows} Mosaic @ {framing.rotation}°",
                           back_url=back_url)


@mobile_bp.route('/m/object/<path:object_name>')
@login_required
def mobile_object_detail(object_name):
    """Mobile-optimized page showing object details with altitude chart and opportunities."""
    load_full_astro_context()

    # Get the object record from config
    obj_record = g.objects_map.get(object_name.lower())
    if not obj_record:
        flash(_("Object '%(object_name)s' not found.", object_name=object_name), "error")
        return redirect(url_for('mobile.mobile_up_now'))

    # Get location details
    lat = g.lat
    lon = g.lon
    tz_name = g.tz_name
    user_prefs = g.user_config or {}

    # Get location object for horizon mask
    location = None
    for loc in g.active_locations:
        if loc == g.selected_location:
            location = g.active_locations[loc]
            break

    if not location:
        flash(_("Location not found."), "error")
        return redirect(url_for('mobile.mobile_up_now'))

    # Calculate object position data (same logic as mobile_up_now)
    try:
        ra = obj_record.get('RA (hours)')
        dec = obj_record.get('DEC (deg)', obj_record.get('DEC (degrees)'))

        if ra is None or dec is None:
            flash(_("Object coordinates not available."), "error")
            return redirect(url_for('mobile.mobile_up_now'))

        ra = float(ra)
        dec = float(dec)

        # Get current datetime in local timezone
        local_tz = pytz.timezone(tz_name)
        current_datetime_local = datetime.now(local_tz)

        # Determine "Observing Night" Date (Noon-to-Noon Logic)
        if current_datetime_local.hour < 12:
            local_date = (current_datetime_local - timedelta(days=1)).strftime('%Y-%m-%d')
        else:
            local_date = current_datetime_local.strftime('%Y-%m-%d')

        # Get calculation settings
        from nova.config import nightly_curves_cache
        sampling_interval = 15
        if SINGLE_USER_MODE:
            sampling_interval = user_prefs.get('sampling_interval_minutes', 15)
        else:
            import os
            sampling_interval = int(os.environ.get('CALCULATION_PRECISION', 15))

        # Get altitude threshold
        altitude_threshold = user_prefs.get("altitude_threshold", 20)
        location_alt_thresh = location.get('altitude_threshold')
        if location_alt_thresh is not None:
            altitude_threshold = location_alt_thresh

        # Calculate horizon mask
        horizon_mask = location.get('horizon_mask', [])
        location_name_key = location.get('name', '').lower().replace(' ', '_')

        # Calculate current position
        from astropy.coordinates import SkyCoord, AltAz, EarthLocation
        from astropy.time import Time
        from astropy import units as u
        from astropy.coordinates import get_body

        location_ephem = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
        time_obj_now = Time(datetime.now(pytz.utc))

        sky_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)

        # Get current altitude/azimuth
        frame_now = AltAz(obstime=time_obj_now, location=location_ephem)
        obj_in_frame = sky_coord.transform_to(frame_now)
        current_alt = obj_in_frame.alt.deg
        current_az = obj_in_frame.az.deg

        # Calculate trend (look at position in 15 minutes)
        import numpy as np
        time_obj_next = Time((datetime.now(pytz.utc) + timedelta(minutes=15)))
        frame_next = AltAz(obstime=time_obj_next, location=location_ephem)
        obj_next = sky_coord.transform_to(frame_next)
        next_alt = obj_next.alt.deg
        trend = '-'
        if abs(next_alt - current_alt) > 0.01:
            trend = '↑' if next_alt > current_alt else '↓'

        # Calculate observable duration
        from modules.astro_calculations import calculate_observable_duration_vectorized, calculate_transit_time
        obs_duration, max_alt, _unused1, _unused2 = calculate_observable_duration_vectorized(
            ra, dec, lat, lon, local_date, tz_name, altitude_threshold, sampling_interval,
            horizon_mask=horizon_mask
        )
        obs_duration_min = int(obs_duration.total_seconds() / 60) if obs_duration else 0

        # Calculate moon separation
        moon_in_frame = get_body('moon', time_obj_now, location=location_ephem)
        moon_coord_sky = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
        obj_in_frame_for_moon = moon_coord_sky.transform_to(frame_now)
        angular_sep = round(obj_in_frame_for_moon.separation(moon_in_frame).deg)

        # Get transit time
        transit_time_str = calculate_transit_time(ra, dec, lat, lon, tz_name, local_date)

        # Get framing status
        db = get_db()
        has_framing = db.query(SavedFraming).filter_by(
            user_id=g.db_user.id, object_name=object_name
        ).first() is not None

    except Exception as e:
        print(f"[Mobile Object Detail] Error calculating object data: {e}")
        import traceback
        traceback.print_exc()
        current_alt = 0
        current_az = 0
        trend = '-'
        obs_duration_min = 0
        angular_sep = 0
        transit_time_str = "Error"
        has_framing = False

    # Get object notes
    notes = obj_record.get('project_name', '')
    if notes and not notes.strip().startswith(("<p>", "<div>", "<ul>", "<ol>", "<figure>", "<blockquote>", "<h1>", "<h2>", "<h3>", "<h4>", "<h5>", "<h6>")):
        # Convert plain text to HTML for display
        escaped_text = bleach.clean(notes, tags=[], strip=True)
        notes_html = escaped_text.replace("\n", "<br>")
    else:
        notes_html = notes

    return render_template('mobile_object_detail.html',
                           object_name=object_name,
                           common_name=obj_record.get('Common Name') or object_name,
                           current_alt=f"{current_alt:.1f}",
                           current_az=f"{current_az:.1f}",
                           trend=trend,
                           moon_sep=angular_sep,
                           obs_duration=obs_duration_min,
                           transit_time=transit_time_str,
                           ra=ra,
                           dec=dec,
                           plot_lat=lat,
                           plot_lon=lon,
                           plot_tz=tz_name,
                           plot_loc_name=g.selected_location,
                           has_framing=has_framing,
                           notes_html=notes_html)


@mobile_bp.route('/m/journal/new', methods=['GET', 'POST'])
@login_required
def mobile_journal_new():
    """Mobile quick journal entry form."""
    load_full_astro_context()
    db = get_db()

    # Get user
    if SINGLE_USER_MODE:
        username = "default"
    else:
        username = current_user.username

    user = db.query(DbUser).filter_by(username=username).one_or_none()
    if not user:
        flash(_("User not found."), "error")
        return redirect(url_for('mobile.mobile_up_now'))

    # Fetch rigs for this user (same as graph_dashboard)
    rigs_from_db = db.query(Rig).options(
        selectinload(Rig.telescope), selectinload(Rig.camera), selectinload(Rig.reducer_extender)
    ).filter_by(user_id=user.id).all()

    # GET request - render form
    if request.method == 'GET':
        prefill_object = request.args.get('object', '')
        today = datetime.now().strftime('%Y-%m-%d')

        # Fetch tracked objects for datalist
        tracked_objects = db.query(AstroObject.object_name)\
            .filter_by(user_id=user.id)\
            .order_by(AstroObject.object_name)\
            .all()
        objects_list = [obj.object_name for obj in tracked_objects]

        # Compute current moon illumination for pre-fill
        moon_illum = None
        try:
            import ephem as _ephem
            _now = pytz.utc.localize(datetime.utcnow())
            moon_illum = int(round(_ephem.Moon(_now).phase))
        except Exception:
            pass

        return render_template('mobile_journal_new.html',
                           locations=g.active_locations,
                           rigs=rigs_from_db,
                           prefill_object=prefill_object,
                           today=today,
                           objects_list=objects_list,
                           moon_illumination=moon_illum)

    # POST request - save journal entry directly
    try:
        # Validate date
        session_date_str = request.form.get("session_date")
        try:
            parsed_date_utc = datetime.strptime(session_date_str, '%Y-%m-%d').date()
        except (ValueError, TypeError):
            flash(_("Invalid date format."), "error")
            target_object_id = request.form.get("target_object_id")
            return redirect(url_for('mobile.mobile_journal_new', object=target_object_id))

        # Handle project selection
        project_id_for_session = None
        project_selection = request.form.get("project_selection")
        if project_selection and project_selection == "new_project":
            new_project_name = request.form.get("new_project_name", "").strip()
            if new_project_name:
                from uuid import uuid4
                new_project = Project(id=uuid4().hex, user_id=user.id, name=new_project_name)
                db.add(new_project)
                db.flush()
                project_id_for_session = new_project.id
                target_object_id = request.form.get("target_object_id", "").strip()
                if target_object_id:
                    new_project.target_object_name = target_object_id
        elif project_selection and project_selection != "standalone":
            project_id_for_session = project_selection

        # Get Rig Snapshot Specs and Component Names
        rig_id_str = request.form.get("rig_id_snapshot")
        rig_id_snap, rig_name_snap, efl_snap, fr_snap, scale_snap, fov_w_snap, fov_h_snap = None, None, None, None, None, None, None
        tel_name_snap, reducer_name_snap, camera_name_snap = None, None, None

        if rig_id_str:
            try:
                rig_id = int(rig_id_str)
                rig = db.query(Rig).options(
                    selectinload(Rig.telescope), selectinload(Rig.camera), selectinload(Rig.reducer_extender)
                ).filter_by(id=rig_id, user_id=user.id).one_or_none()

                if rig:
                    rig_id_snap = rig.id
                    rig_name_snap = rig.rig_name
                    efl_snap, fr_snap, scale_snap, fov_w_snap = _compute_rig_metrics_from_components(
                        rig.telescope, rig.camera, rig.reducer_extender
                    )
                    if rig.camera and rig.camera.sensor_height_mm and efl_snap:
                        fov_h_snap = (degrees(2 * atan((rig.camera.sensor_height_mm / 2.0) / efl_snap)) * 60.0)

                    tel_name_snap = rig.telescope.name if rig.telescope else None
                    reducer_name_snap = rig.reducer_extender.name if rig.reducer_extender else None
                    camera_name_snap = rig.camera.name if rig.camera else None
            except (ValueError, TypeError):
                pass  # rig_id_str was invalid (e.g., "")

        # Import JournalSession and Project models
        from nova.models import JournalSession, Project
        from uuid import uuid4
        from nova.helpers import safe_float, safe_int, generate_session_id
        import json

        # Create New Session Object
        new_session = JournalSession(
            user_id=user.id,
            project_id=project_id_for_session,
            date_utc=parsed_date_utc,
            object_name=request.form.get("target_object_id", "").strip(),
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
            rig_id_snapshot=rig_id_snap,
            rig_name_snapshot=rig_name_snap,
            rig_efl_snapshot=efl_snap,
            rig_fr_snapshot=fr_snap,
            rig_scale_snapshot=scale_snap,
            rig_fov_w_snapshot=fov_w_snap,
            rig_fov_h_snapshot=fov_h_snap,
            telescope_name_snapshot=tel_name_snap,
            reducer_name_snapshot=reducer_name_snap,
            camera_name_snapshot=camera_name_snap
        )

        # Custom filter data (user-defined filters stored as JSON)
        from nova.models import UserCustomFilter
        custom_data = {}
        for cf in db.query(UserCustomFilter).filter_by(user_id=user.id).all():
            subs = request.form.get(f'filter_{cf.filter_key}_subs')
            exp = request.form.get(f'filter_{cf.filter_key}_exposure_sec')
            if subs or exp:
                custom_data[f'filter_{cf.filter_key}_subs'] = int(subs) if subs else None
                custom_data[f'filter_{cf.filter_key}_exposure_sec'] = int(exp) if exp else None
        new_session.custom_filter_data = json.dumps(custom_data) if custom_data else None

        # Total exposure calculation
        FIXED_FILTER_KEYS = ['L', 'R', 'G', 'B', 'Ha', 'OIII', 'SII']
        total_seconds = 0

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

        new_session.calculated_integration_time_minutes = round(total_seconds / 60.0, 1) if total_seconds > 0 else None

        # Add to database
        db.add(new_session)
        db.flush()
        db.commit()

        # Flash success message
        flash(_("New journal entry added successfully!"), "success")
        from nova.analytics import record_event
        record_event('journal_session_created')

        # Redirect to mobile object detail page if target object specified, otherwise mobile home
        target_object_id = new_session.object_name
        if target_object_id:
            return redirect(url_for('mobile.mobile_object_detail', object_name=target_object_id))
        else:
            return redirect(url_for('mobile.mobile_up_now'))

    except Exception as e:
        db.rollback()
        print(f"[Mobile Journal New] Error: {e}")
        import traceback
        traceback.print_exc()
        flash(_("Error saving journal entry: %(error)s", error=str(e)), "error")
        target_object_id = request.form.get("target_object_id", "")
        return redirect(url_for('mobile.mobile_journal_new', object=target_object_id))


@mobile_bp.route('/sw.js')
def service_worker():
    """Serve the service worker from root scope (/sw.js) to cover /m/ paths."""
    from flask import send_from_directory, Response
    import os.path

    sw_path = os.path.join(current_app.static_folder, 'sw.js')
    if not os.path.exists(sw_path):
        return Response("Service worker not found", status=404)

    with open(sw_path, 'r', encoding='utf-8') as f:
        sw_content = f.read()

    response = Response(sw_content, mimetype='application/javascript')
    response.headers['Service-Worker-Allowed'] = '/'
    return response
