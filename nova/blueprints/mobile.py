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

# =============================================================================
# Third-Party Imports
# =============================================================================
import bleach
from flask import (
    Blueprint, render_template, redirect, url_for, flash,
    request, g
)
from flask_login import login_required, current_user
from flask_babel import gettext as _

# =============================================================================
# Nova Package Imports (no circular import)
# =============================================================================
from nova import SINGLE_USER_MODE  # Import from nova for test patching compatibility
from nova.models import (
    DbUser, AstroObject, SavedFraming, Rig
)
from nova.helpers import (
    get_db, load_full_astro_context
)
from nova.analytics import record_event


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
    return f"{sign}{d:02d}º {m:02d}' {s:02d}\""


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
    obj_record = g.objects_map.get(object_name)
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
        if loc.name == g.selected_location:
            location = loc
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
        if location.altitude_threshold is not None:
            altitude_threshold = location.altitude_threshold

        # Calculate horizon mask
        horizon_mask = [[hp.az_deg, hp.alt_min_deg] for hp in sorted(location.horizon_points, key=lambda p: p.az_deg)]
        location_name_key = location.name.lower().replace(' ', '_')

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
        obs_duration, max_alt, _, _ = calculate_observable_duration_vectorized(
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
        angular_sep = "N/A"
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
                           common_name=obj_record.get('common_name', object_name),
                           current_alt=f"{current_alt:.1f}",
                           current_az=f"{current_az:.1f}",
                           trend=trend,
                           moon_sep=angular_sep if angular_sep != "N/A" else "N/A",
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
