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
    return f"{h:02d}hr {m:02d}' {s:02d}\""


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
