"""AI blueprint routes.

Flask blueprint for AI-related API endpoints. Only registers if AI_API_KEY
is present in app.config.
"""

import logging
import re
from datetime import datetime, time, timedelta

import ephem
import pytz
from astropy.coordinates import EarthLocation, AltAz, SkyCoord, get_body
from astropy.time import Time
import astropy.units as u

from flask import Blueprint, current_app, jsonify, g, request, Response, stream_with_context
from sqlalchemy.orm import selectinload

from nova.ai.config import ai_enabled, user_has_ai_access
from nova.ai.prompts import (
    build_dso_notes_prompt,
    build_session_summary_prompt,
    build_best_objects_prompt,
)
from nova.ai.service import get_ai_response, AIServiceError
import math

from nova.helpers import get_db
from nova.models import AstroObject, Location, Rig, JournalSession, SavedFraming

logger = logging.getLogger(__name__)


def compress_objects_for_prompt(objects: list[dict]) -> str:
    """Converts object dicts to compact pipe-delimited lines for AI prompt.

    Format: NAME|TYPE|OBSm|altALT|moonMOON|magMAG|SIZE'
    Example: M42|HII|180m|alt52|moon60|mag4.0|66'

    Args:
        objects: List of object dicts with keys: Object, Type, Magnitude,
                Size, Observable Duration (min), Max Altitude (°),
                Angular Separation (°)

    Returns:
        String of newline-separated compressed object lines.

    Rules:
    - obs_duration: round to int
    - max_altitude: round to 1 decimal
    - moon_separation: round to 1 decimal
    - magnitude: convert to float, round to 2 decimals, if >= 90 or unknown → 'mag?'
    - size: convert to float, round to 1 decimal, if 'Not Found' or null/0/empty → 'size?'
    - join all lines with newline
    """
    lines = []
    for obj in objects:
        name = obj.get("Object", "")
        obj_type = obj.get("Type", "")
        obs_duration = obj.get("Observable Duration (min)")
        max_altitude = obj.get("Max Altitude (°)")
        moon_sep = obj.get("Angular Separation (°)")
        magnitude = obj.get("Magnitude")
        size = obj.get("Size")

        # Round obs_duration to int
        obs_str = f"{int(obs_duration)}m" if obs_duration is not None else "obs?"

        # Round max_altitude to 1 decimal
        alt_str = f"alt{max_altitude:.1f}" if max_altitude is not None else "alt?"

        # Round moon separation to 1 decimal
        moon_str = f"moon{moon_sep:.1f}" if moon_sep is not None else "moon?"

        # Handle magnitude: convert to float, round to 2 decimals, if parse fails or >= 99 → mag?
        if magnitude is None or magnitude == 99 or magnitude == "unknown":
            mag_str = "mag?"
        else:
            try:
                mag_val = float(magnitude)
                if mag_val >= 99:
                    mag_str = "mag?"
                else:
                    mag_str = f"mag{round(mag_val, 2)}"
            except (ValueError, TypeError):
                mag_str = "mag?"

        # Handle size: convert to float, round to 1 decimal, if parse fails or 0/null/empty → size?
        if size is None or size == "Not Found" or size == 0 or (isinstance(size, str) and size.strip() == ""):
            size_str = "size?"
        else:
            # Extract numeric part for size (arcmin)
            try:
                size_val = float(size)
                size_str = f"{round(size_val, 1)}'"
            except (ValueError, TypeError):
                size_str = "size?"
        lines.append(f"{name}|{obj_type}|{obs_str}|{alt_str}|{moon_str}|{mag_str}|{size_str}")

    return "\n".join(lines)


def pre_filter_objects(objects, user_settings, moon_illumination_pct, max_aperture_mm=None):
    """Pre-filter objects based on user settings and conditions.

    Filters are applied in order, fail-fast per object. Returns both filtered
    objects and a counts dictionary for debugging/logging.

    Args:
        objects: List of object dicts with keys:
            - Object (name)
            - enabled (bool)
            - Magnitude (string or float)
            - Size (string representing arcmin)
            - Observable Duration (min) (float)
            - Max Altitude (°) (float)
            - Angular Separation (°) (float)  # Note: typo preserved for compatibility
        user_settings: Dict with keys:
            - min_observable_minutes (int)
            - min_max_altitude (float)
        moon_illumination_pct: Float (0-100), moon illumination percentage
        max_aperture_mm: Optional - largest aperture in mm from user's rigs. If None,
                         skip magnitude filter entirely.

    Returns:
        Tuple: (filtered_objects, counts_dict)
        filtered_objects: List of objects that pass all filters
        counts_dict: Dict with filter stage counts
    """
    # Calculate limiting magnitude if aperture is available
    # Formula: limiting_mag = 2.1 + 5 * log10(aperture_mm)
    limiting_mag = None
    if max_aperture_mm and max_aperture_mm > 0:
        limiting_mag = 2.1 + 5 * math.log10(max_aperture_mm)
    # Initialize counts
    counts = {
        "total_in": len(objects),
        "enabled_count": 0,
        "after_obs": 0,
        "after_alt": 0,
        "after_moon": 0,
        "after_size": 0,
        "after_magnitude": 0,
        "total_out": 0
    }

    # Extract settings with defaults
    min_obs_duration = user_settings.get("min_observable_minutes", 60)
    min_max_altitude = user_settings.get("min_max_altitude", 30)

    # Helper to parse angular size from Size string
    # Expected formats: "10'" (arcmin), "10x5'" (rectangular), "0.5°" (degrees)
    def parse_angular_size(size_str):
        if not size_str:
            return None
        try:
            size_str = str(size_str).strip()
            # Check for arcmin format (e.g., "10'", "10'", "10'")
            if "'" in size_str:
                # Remove the arcmin symbol and convert to float
                size_str = size_str.replace("'", "").replace("′", "").replace("′", "")
                # Handle rectangular format like "10x5"
                if "x" in size_str.lower():
                    parts = size_str.lower().split("x")
                    # Average the dimensions
                    values = [float(p) for p in parts if p]
                    return sum(values) / len(values) if values else None
                return float(size_str)
            # Check for degree format (e.g., "0.5°")
            elif "°" in size_str:
                degrees = float(size_str.replace("°", ""))
                return degrees * 60  # Convert to arcmin
            else:
                # Try direct float
                return float(size_str)
        except (ValueError, AttributeError):
            return None

    filtered = []
    for obj in objects:
        # Filter 1: enabled == True only
        if not obj.get("enabled", True):
            continue
        counts["enabled_count"] += 1

        # Filter 2: obs_duration >= user_settings.min_observable_duration
        obs_duration = obj.get("Observable Duration (min)")
        if obs_duration is None or obs_duration < min_obs_duration:
            continue
        counts["after_obs"] += 1

        # Filter 3: max_altitude >= user_settings.min_max_altitude
        max_altitude = obj.get("Max Altitude (°)")
        if max_altitude is None or max_altitude < min_max_altitude:
            continue
        counts["after_alt"] += 1

        # Filter 4: moon_separation >= moon_illumination_pct * 0.55
        moon_sep = obj.get("Angular Separation (°)")
        min_moon_sep = moon_illumination_pct * 0.55
        if moon_sep is not None and moon_sep < min_moon_sep:
            continue
        counts["after_moon"] += 1

        # Filter 5: angular_size >= 3 arcmin OR angular_size is null/0/unknown
        angular_size = parse_angular_size(obj.get("Size"))
        if angular_size is not None and angular_size < 3:
            continue
        counts["after_size"] += 1

        # Filter 6: magnitude check
        # If magnitude == 99 or null → keep
        # Else keep only if within rig's aperture-based limiting magnitude
        magnitude = obj.get("Magnitude")
        if magnitude is not None:
            try:
                mag_val = float(magnitude)
                if mag_val >= 90:  # Sentinel value for "unknown"
                    # Keep - unknown magnitude
                    pass
                elif limiting_mag is not None and mag_val > limiting_mag:
                    # Object is too faint for largest aperture
                    continue
                # Else: magnitude is known and within limits - keep
            except (ValueError, TypeError):
                # Invalid magnitude format - skip
                continue

        counts["after_magnitude"] += 1
        filtered.append(obj)

    counts["total_out"] = len(filtered)
    return filtered, counts

ai_bp = Blueprint("ai", __name__)


@ai_bp.route("/api/ai/status", methods=["GET"])
def get_ai_status():
    """Return AI availability status for the current session user.

    Returns:
        JSON: {"enabled": true/false}
    """
    # Check if AI is globally configured
    if not ai_enabled():
        return jsonify({"enabled": False})

    # Get the current user from g (set by request context middleware)
    username = getattr(g, "db_user", None)
    if username is not None:
        username = getattr(username, "username", None)

    if not username:
        return jsonify({"enabled": False})

    # Check if this user has AI access
    enabled = user_has_ai_access(username)
    return jsonify({"enabled": enabled})


@ai_bp.route("/api/ai/notes", methods=["POST"])
def generate_dso_notes():
    """Generate AI-assisted observing notes for a DSO object.

    Request body:
        JSON: {"object_id": int}

    Returns:
        JSON: {"notes": str} on success
        JSON: {"error": str} on error (400/403/404/500/503)
    """
    # Guard: check AI access for current user
    username = getattr(g, "db_user", None)
    if username is not None:
        username = getattr(username, "username", None)

    if not username or not user_has_ai_access(username):
        return jsonify({"error": "AI access not enabled for this account"}), 403

    # Validate request body
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body is required"}), 400

    object_id = data.get("object_id")
    object_name = data.get("object_name")
    selected_day = data.get("selected_day")
    selected_month = data.get("selected_month")
    selected_year = data.get("selected_year")
    sim_mode = data.get("sim_mode", False)

    # Fetch the DSO object from database
    db = get_db()
    obj = None

    if object_id and object_id != 0:
        obj = db.query(AstroObject).filter(AstroObject.id == object_id).first()
    elif object_name:
        obj = db.query(AstroObject).filter(AstroObject.object_name == object_name).first()
    else:
        return jsonify({"error": "object_id or object_name is required"}), 400

    if not obj:
        return jsonify({"error": "Object not found"}), 404

    # Build object_data dict from fetched object
    object_data = {
        "name": getattr(obj, "object_name", None) or getattr(obj, "common_name", None),
        "type": getattr(obj, "type", None),
        "constellation": getattr(obj, "constellation", None),
        "magnitude": getattr(obj, "magnitude", None),
        "size_arcmin": getattr(obj, "size", None),
        "ra": getattr(obj, "ra_hours", None),
        "dec": getattr(obj, "dec_deg", None),
    }

    # Get current locale using lazy import to avoid circular dependency
    from nova import get_locale
    locale = get_locale()

    # Gather location context
    loc_rows = db.query(Location).filter_by(
        user_id=g.db_user.id, active=True
    ).order_by(Location.id).all()

    locations = [
        {
            "name": loc.name,
            "lat": loc.lat,
            "lon": loc.lon,
            "timezone": loc.timezone,
            "is_default": loc.is_default,
            "altitude_threshold": loc.altitude_threshold,
        }
        for loc in loc_rows
    ]

    active_location = next(
        (l for l in locations if l["is_default"]),
        locations[0] if locations else None
    )

    # Calculate moon phase, separation, and target data for sim_mode
    moon_phase = None
    moon_separation = None
    target_altitude_deg = None
    target_transit_time = None
    if sim_mode and selected_day and selected_month and selected_year and active_location:
        try:
            from modules.astro_calculations import calculate_transit_time

            lat = active_location["lat"]
            lon = active_location["lon"]
            tz_name = active_location.get("timezone", "UTC")

            # Create date object
            date_obj = datetime(int(selected_year), int(selected_month), int(selected_day))
            date_str = date_obj.strftime('%Y-%m-%d')

            # Use 11 PM local for moon phase and separation (matches dashboard "Ang. Sep." column)
            local_tz = pytz.timezone(tz_name)
            time_11pm_local = local_tz.localize(datetime.combine(date_obj, time(23, 0)))
            dt_utc = time_11pm_local.astimezone(pytz.utc)

            # Moon Phase
            moon_phase = round(ephem.Moon(dt_utc).phase, 1)

            # Angular Separation between moon and target object at 11 PM local
            time_obj_sep = Time(dt_utc)
            loc_obj_sep = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
            moon_coord_sep = get_body('moon', time_obj_sep, loc_obj_sep)
            obj_coord_sep = SkyCoord(ra=obj.ra_hours * u.hourangle, dec=obj.dec_deg * u.deg)
            frame_sep = AltAz(obstime=time_obj_sep, location=loc_obj_sep)

            sep_val = obj_coord_sep.transform_to(frame_sep).separation(moon_coord_sep.transform_to(frame_sep)).deg
            moon_separation = round(sep_val)

            # Calculate target transit time first (needed for max altitude)
            target_transit_time = calculate_transit_time(
                obj.ra_hours, obj.dec_deg, lat, lon, tz_name, date_str
            )

            # Calculate max altitude using calculate_observable_duration_vectorized (same as dashboard)
            # This is the same value the dashboard shows in the MAX ALTITUDE column
            from modules.astro_calculations import calculate_observable_duration_vectorized
            altitude_threshold = active_location.get("altitude_threshold", 20)
            _, target_altitude_deg, _, _ = calculate_observable_duration_vectorized(
                obj.ra_hours, obj.dec_deg, lat, lon, date_str, tz_name, altitude_threshold
            )
            target_altitude_deg = round(target_altitude_deg, 1) if target_altitude_deg is not None else None
        except Exception as e:
            logger.warning(f"Failed to calculate moon phase/separation/target data: {e}")
            moon_phase = None
            moon_separation = None
            target_altitude_deg = None
            target_transit_time = None

    # Gather rig context
    rig_rows = db.query(Rig).options(
        selectinload(Rig.telescope),
        selectinload(Rig.camera),
        selectinload(Rig.reducer_extender)
    ).filter_by(user_id=g.db_user.id).all()

    rigs = []
    for rig in rig_rows:
        # Detect camera type (OSC vs mono) from camera name
        cam_name = rig.camera.name if rig.camera else None
        cam_name_lower = (cam_name or "").lower()
        is_mono = any(x in cam_name_lower for x in ["mm", "mono", " m "])
        camera_type = "mono" if is_mono else "OSC"

        rigs.append({
            "name": rig.rig_name,
            "effective_focal_length": rig.effective_focal_length,
            "f_ratio": rig.f_ratio,
            "fov_w_arcmin": rig.fov_w_arcmin,
            "image_scale": rig.image_scale,
            "aperture_mm": rig.telescope.aperture_mm if rig.telescope else None,
            "camera_type": camera_type,
            "telescope": {
                "name": rig.telescope.name if rig.telescope else None,
                "aperture_mm": rig.telescope.aperture_mm if rig.telescope else None,
                "focal_length_mm": rig.telescope.focal_length_mm if rig.telescope else None,
            } if rig.telescope else None,
            "camera": {
                "name": rig.camera.name if rig.camera else None,
                "sensor_width_mm": rig.camera.sensor_width_mm if rig.camera else None,
                "pixel_size_um": rig.camera.pixel_size_um if rig.camera else None,
            } if rig.camera else None,
        })

    # Query for saved framing for this object
    framing = db.query(SavedFraming).filter_by(
        user_id=g.db_user.id,
        object_name=obj.object_name
    ).one_or_none()

    rig = None
    if framing and framing.rig_id:
        rig = db.query(Rig).options(
            selectinload(Rig.telescope),
            selectinload(Rig.camera)
        ).filter_by(id=framing.rig_id).one_or_none()

        if rig is None and framing.rig_name:
            rig = next((r for r in rig_rows if r.rig_name == framing.rig_name), None)

    # Build framing context if a saved framing exists with a rig
    framing_context = None
    if rig:
        # Calculate FOV height from sensor dimensions and focal length
        # Formula: (sensor_height_mm / focal_length_mm) * 3437.75 = arcmin
        fov_h_arcmin = None
        if rig.camera and rig.camera.sensor_height_mm and rig.effective_focal_length:
            fov_h_arcmin = (rig.camera.sensor_height_mm / rig.effective_focal_length) * 3437.75

        framing_context = {
            "rig_name": rig.rig_name,
            "telescope_name": rig.telescope.name if rig.telescope else None,
            "focal_length_mm": rig.effective_focal_length,
            "sensor_width_mm": rig.camera.sensor_width_mm if rig.camera else None,
            "pixel_scale_arcsec_px": rig.image_scale,
            "fov_w_deg": (rig.fov_w_arcmin / 60) if rig.fov_w_arcmin else None,
            "fov_h_deg": (fov_h_arcmin / 60) if fov_h_arcmin else None,
            "f_ratio": rig.f_ratio,
        }

    try:
        # Build the prompt
        prompt = build_dso_notes_prompt(
            object_data,
            locations=locations,
            active_location=active_location,
            rigs=rigs,
            locale=locale,
            selected_day=selected_day,
            selected_month=selected_month,
            selected_year=selected_year,
            sim_mode=sim_mode,
            moon_phase=moon_phase,
            moon_separation=moon_separation,
            target_altitude_deg=target_altitude_deg,
            target_transit_time=target_transit_time,
            framing_context=framing_context,
        )

        # Get AI response
        notes = get_ai_response(prompt["user"], system=prompt["system"], max_tokens=1500)

        # Convert plain text to HTML paragraphs for Trix editor
        import re
        notes = notes.strip()
        raw_paragraphs = [p.strip() for p in re.split(r'\n+', notes) if p.strip()]
        html_parts = []
        for p in raw_paragraphs:
            if p.startswith('<p>'):
                html_parts.append(p)
            else:
                html_parts.append(f'<p>{p}</p>')
        notes = ''.join(html_parts)

        return jsonify({"notes": notes})

    except AIServiceError as e:
        logger.error(f"AIServiceError in /api/ai/notes: {str(e)}")
        return jsonify({"error": str(e)}), 503

    except Exception as e:
        logger.exception("Unexpected error generating DSO notes")
        return jsonify({"error": "An unexpected error occurred"}), 500


@ai_bp.route("/api/ai/session-summary", methods=["POST"])
def generate_session_summary():
    """Generate AI-assisted summary for a journal session (streaming).

    Request body:
        JSON: {"session_id": int}

    Returns:
        SSE stream: "data: <chunk>\n\n" for each text chunk
        JSON error on failure (400/403/404/500/503)
    """
    # Guard: check AI access for current user
    username = getattr(g, "db_user", None)
    if username is not None:
        username = getattr(username, "username", None)

    if not username or not user_has_ai_access(username):
        return jsonify({"error": "AI access not enabled for this account"}), 403

    # Validate request body
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body is required"}), 400

    session_id = data.get("session_id")
    if not session_id:
        return jsonify({"error": "session_id is required"}), 400

    # Fetch the session from database
    db = get_db()
    session = db.query(JournalSession).filter(JournalSession.id == session_id).first()

    if not session:
        return jsonify({"error": "Session not found"}), 404

    # Verify session belongs to current user
    if session.user_id != g.db_user.id:
        return jsonify({"error": "Session not found"}), 404

    # Build session_data dict from session fields
    session_data = {
        "object_name": session.object_name,
        "date_utc": session.date_utc.isoformat() if session.date_utc else None,
        "location_name": session.location_name,
        "calculated_integration_time_minutes": session.calculated_integration_time_minutes,
        "number_of_subs_light": session.number_of_subs_light,
        "exposure_time_per_sub_sec": session.exposure_time_per_sub_sec,
        "filter_used_session": session.filter_used_session,
        "rig_name_snapshot": session.rig_name_snapshot,
        "telescope_name_snapshot": session.telescope_name_snapshot,
        "camera_name_snapshot": session.camera_name_snapshot,
        "rig_efl_snapshot": session.rig_efl_snapshot,
        "rig_fr_snapshot": session.rig_fr_snapshot,
        "imaging_scale_arcsec_px": session.rig_scale_snapshot,
        "seeing_observed_fwhm": session.seeing_observed_fwhm,
        "sky_sqm_observed": session.sky_sqm_observed,
        "guiding_rms_avg_arcsec": session.guiding_rms_avg_arcsec,
        "moon_illumination_session": session.moon_illumination_session,
        "moon_angular_separation_session": session.moon_angular_separation_session,
        "camera_temp_actual_avg_c": session.camera_temp_actual_avg_c,
        "gain_setting": session.gain_setting,
        "session_rating_subjective": session.session_rating_subjective,
        "transparency_observed_scale": session.transparency_observed_scale,
        "weather_notes": session.weather_notes,
        "telescope_setup_notes": session.telescope_setup_notes,
        "dither_notes": session.dither_notes,
        "darks_strategy": session.darks_strategy,
        "flats_strategy": session.flats_strategy,
        "general_notes_problems_learnings": session.notes,  # stored in 'notes' column
    }

    # Fetch guide hardware data from rig snapshot if available
    guide_pixel_um = None
    guide_FL_mm = None

    if session.rig_id_snapshot:
        rig = db.query(Rig).filter(Rig.id == session.rig_id_snapshot).first()
        if rig and (not rig.guide_camera_id or not rig.guide_telescope_id):
            # Fallback: try to find rig by partial name match if guide hardware IDs are missing
            rig = db.query(Rig).filter(
                Rig.rig_name.ilike(f"%{session.rig_name_snapshot}%"),
                Rig.guide_camera_id.isnot(None)
            ).first()

        if rig:
            guide_pixel_um = rig.guide_camera.pixel_size_um if rig.guide_camera else None

            if rig.guide_is_oag:
                guide_FL_mm = rig.effective_focal_length
            elif rig.guide_telescope:
                guide_FL_mm = rig.guide_telescope.focal_length_mm

    session_data["guide_pixel_um"] = guide_pixel_um
    session_data["guide_FL_mm"] = guide_FL_mm

    # Guide binning is not tracked in the data model
    session_data["guide_binning_note"] = "Binning not tracked — check PHD2/ASIAIR config manually"

    # Process log_analysis_cache
    log_analysis_summary = {}
    if session.log_analysis_cache:
        try:
            import json
            cached = json.loads(session.log_analysis_cache)

            # Extract ASIAIR stats
            asiair = cached.get("asiair")
            if asiair and asiair.get("stats"):
                log_analysis_summary["asiair_stats"] = asiair["stats"]
                # Calculate dither statistics
                dithers = asiair.get("dithers", [])
                if dithers:
                    successful_dithers = [d for d in dithers if d.get("ok")]
                    timeout_dithers = [d for d in dithers if not d.get("ok")]
                    if successful_dithers:
                        avg_settle_seconds = sum(d.get("dur", 0) for d in successful_dithers) / len(successful_dithers)
                        log_analysis_summary["asiair_stats"]["avg_settle_seconds"] = round(avg_settle_seconds, 1)
                    total_dither_time = sum(d.get("dur", 0) for d in dithers)
                    log_analysis_summary["asiair_stats"]["total_dither_time_sec"] = round(total_dither_time, 1)
                    if timeout_dithers:
                        log_analysis_summary["asiair_stats"]["avg_timeout_duration_sec"] = round(
                            sum(d.get("dur", 0) for d in timeout_dithers) / len(timeout_dithers), 1
                        )

            # Extract PHD2 stats
            phd2 = cached.get("phd2")
            if phd2 and phd2.get("stats"):
                log_analysis_summary["phd2_stats"] = phd2["stats"]
                # Calculate dither/settle statistics
                settles = phd2.get("settle", [])
                if settles:
                    successful_settles = [s for s in settles if s.get("ok")]
                    timeout_settles = [s for s in settles if not s.get("ok")]
                    if successful_settles:
                        avg_settle_seconds = sum(s.get("dur", 0) for s in successful_settles) / len(successful_settles)
                        log_analysis_summary["phd2_stats"]["avg_settle_seconds"] = round(avg_settle_seconds, 1)
                    total_settle_time = sum(s.get("dur", 0) for s in settles)
                    log_analysis_summary["phd2_stats"]["total_settle_time_sec"] = round(total_settle_time, 1)
                    if timeout_settles:
                        log_analysis_summary["phd2_stats"]["avg_timeout_duration_sec"] = round(
                            sum(s.get("dur", 0) for s in timeout_settles) / len(timeout_settles), 1
                        )

            # Extract NINA summary
            nina = cached.get("nina")
            if nina:
                nina_summary = {
                    "autofocus_runs": len(nina.get("autofocus_runs", [])),
                    "error_count": nina.get("error_count", len(nina.get("errors", []))),
                    "warning_count": nina.get("warning_count", len(nina.get("warnings", []))),
                }
                # Count failed autofocus runs
                failed_af = sum(1 for af in nina.get("autofocus_runs", []) if af.get("status") == "failed")
                if failed_af > 0:
                    nina_summary["failed_autofocus"] = failed_af
                log_analysis_summary["nina_summary"] = nina_summary
        except (json.JSONDecodeError, KeyError, AttributeError):
            # If cache is invalid, just skip log analysis
            pass

    session_data["log_analysis_summary"] = log_analysis_summary

    # Strip HTML tags from general_notes_problems_learnings before passing to prompt
    notes = session_data.get("general_notes_problems_learnings")
    if notes:
        # Remove HTML tags but preserve content
        session_data["general_notes_problems_learnings"] = re.sub(r'<[^>]+>', ' ', notes).strip()

    # Get current locale using lazy import to avoid circular dependency
    from nova import get_locale
    locale = get_locale()

    # Build the prompt
    prompt = build_session_summary_prompt(session_data, locale=locale)

    def generate():
        """Generate SSE stream from AI provider chunks."""
        try:
            # Buffer to accumulate all streamed content for post-processing
            full_content = []
            buffer = ""

            for chunk in get_ai_response(prompt["user"], system=prompt["system"], stream=True, max_tokens=3000):
                if not chunk:
                    continue

                buffer += chunk

                # When we have newlines, convert completed paragraphs to HTML and emit
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    STRAY_QUOTES = {'"', '\u201c', '\u201d', '`', "'"}
                    if line and line not in STRAY_QUOTES:
                        # Emit paragraph and also store for post-processing
                        full_content.append(f"<p>{line}</p>")
                        yield f"data: <p>{line}</p>\n\n"
                    else:
                        # Empty line just emits a paragraph break
                        yield f"data: \n\n"

            # Emit any remaining content in the buffer
            remaining = buffer.strip()
            STRAY_QUOTES = {'"', '\u201c', '\u201d', '`', "'"}
            if remaining and remaining not in STRAY_QUOTES:
                full_content.append(f"<p>{remaining}</p>")
                yield f"data: <p>{remaining}</p>\n\n"

            complete_text = "".join(full_content)
            fixed_text = '\n'.join(
                f'<p>{l.strip()}</p>'
                for l in complete_text.split('\n')
                if l.strip()
            )
            if fixed_text:
                yield f"data: [CORRECT]{fixed_text}\n\n"
            yield "data: [DONE]\n\n"

        except AIServiceError as e:
            logger.error(f"AIServiceError in /api/ai/session-summary: {str(e)}")
            yield f"data: [ERROR]{str(e)}\n\n"
        except Exception as e:
            logger.exception("Unexpected error generating session summary")
            yield f"data: [ERROR]An unexpected error occurred\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


@ai_bp.route("/api/ai/best_objects", methods=["POST"])
def get_best_objects():
    """Generate AI-assisted ranking of best objects for the current conditions.

    Request body:
        JSON: {
            "object_list": list of object dicts with keys: Object, Common Name, Type,
                     Magnitude, Size, Constellation, Altitude, Azimuth,
                     Observable Duration (min), Max Altitude (°),
                     Angular Separation (°), etc.
            "location_name": str, name of the observing location
            "sim_date": str or None, date in YYYY-MM-DD format (None for live mode)
        }

    Returns:
        JSON: {
            "ranked_objects": [
                {"Object": str, "rank": int, "reason": str, "recommended_rig": str or None},
                ...
            ]
        } on success
        JSON: {"error": str} on error (400/403/404/500/503)
    """
    # Guard: check AI access for current user
    username = getattr(g, "db_user", None)
    if username is not None:
        username = getattr(username, "username", None)

    if not username or not user_has_ai_access(username):
        return jsonify({"error": "AI access not enabled for this account"}), 403

    # Validate request body
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body is required"}), 400

    object_list = data.get("object_list", [])
    location_name = data.get("location_name")
    sim_date = data.get("sim_date")

    if not object_list:
        return jsonify({"error": "object_list is required"}), 400

    if not location_name:
        return jsonify({"error": "location_name is required"}), 400

    # Get current locale
    from nova import get_locale
    locale = get_locale()

    # Fetch location details
    db = get_db()
    location = db.query(Location).filter_by(
        user_id=g.db_user.id, name=location_name
    ).first()

    if not location:
        return jsonify({"error": "Location not found"}), 404

    # Get moon illumination for date/location
    moon_phase = None
    try:
        # Safe fallback timezone (used for sim_mode)
        local_tz = pytz.timezone(location.timezone or "UTC")

        if sim_date:
            # Sim mode: use the provided date
            date_obj = datetime.strptime(sim_date, "%Y-%m-%d")
        else:
            # Live mode: use current date at 11 PM local time
            now_local = datetime.now(local_tz)
            # If it's before noon, use "night of" previous day
            if now_local.hour < 12:
                date_obj = now_local.date() - timedelta(days=1)
            else:
                date_obj = now_local.date()

        # Use 11 PM local for moon phase calculation (matches dashboard)
        time_11pm_local = local_tz.localize(
            datetime.combine(date_obj, time(23, 0))
        )
        dt_utc = time_11pm_local.astimezone(pytz.utc)
        moon_phase = round(ephem.Moon(dt_utc).phase, 1)
    except Exception as e:
        logger.warning(f"Failed to calculate moon phase: {e}")
        moon_phase = None

    # Get user imaging criteria settings for pre-filter
    user_settings = {
        "min_observable_minutes": 60,
        "min_max_altitude": 30,
    }
    imaging_criteria = getattr(g, "user_config", {}).get("imaging_criteria", {})
    if imaging_criteria:
        user_settings["min_observable_minutes"] = imaging_criteria.get("min_observable_minutes", 60)
        user_settings["min_max_altitude"] = imaging_criteria.get("min_max_altitude", 30)

    # Gather rig context and find max aperture
    rig_rows = db.query(Rig).options(
        selectinload(Rig.telescope),
        selectinload(Rig.camera),
        selectinload(Rig.reducer_extender)
    ).filter_by(user_id=g.db_user.id).all()

    rigs = []
    max_aperture_mm = None
    for rig in rig_rows:
        # Detect camera type (OSC vs mono) from camera name
        cam_name = rig.camera.name if rig.camera else None
        cam_name_lower = (cam_name or "").lower()
        is_mono = any(x in cam_name_lower for x in ["mm", "mono", " m "])
        camera_type = "mono" if is_mono else "OSC"

        # Track max aperture
        if rig.telescope and rig.telescope.aperture_mm:
            if max_aperture_mm is None or rig.telescope.aperture_mm > max_aperture_mm:
                max_aperture_mm = rig.telescope.aperture_mm

        rigs.append({
            "name": rig.rig_name,
            "effective_focal_length": rig.effective_focal_length,
            "f_ratio": rig.f_ratio,
            "fov_w_arcmin": rig.fov_w_arcmin,
            "image_scale": rig.image_scale,
            "aperture_mm": rig.telescope.aperture_mm if rig.telescope else None,
            "camera_type": camera_type,
            "telescope": {
                "name": rig.telescope.name if rig.telescope else None,
                "aperture_mm": rig.telescope.aperture_mm if rig.telescope else None,
                "focal_length_mm": rig.telescope.focal_length_mm if rig.telescope else None,
            } if rig.telescope else None,
            "camera": {
                "name": rig.camera.name if rig.camera else None,
                "sensor_width_mm": rig.camera.sensor_width_mm if rig.camera else None,
                "pixel_size_um": rig.camera.pixel_size_um if rig.camera else None,
            } if rig.camera else None,
        })

    # Run pre_filter_objects on incoming object list
    filtered_objects, counts = pre_filter_objects(
        object_list,
        user_settings,
        moon_phase if moon_phase is not None else 0,
        max_aperture_mm
    )

    logger.info(f"Pre-filter counts: {counts}")

    # If no objects survive pre-filter, return empty results with debug info
    if not filtered_objects:
        return jsonify({
            "ranked_objects": [],
            "debug_filter_counts": counts,
            "debug_post_trim_count": 0,
            "debug_objects_sent_to_ai": 0,
            "debug_prompt_object_block": ""
        })

    objects_for_prompt = filtered_objects

    # Compress objects for AI prompt
    compressed_objects = compress_objects_for_prompt(objects_for_prompt)

    try:
        # Build single AI ranking prompt with compressed format
        ranking_prompt = build_best_objects_prompt(
            objects=objects_for_prompt,
            location_name=location_name,
            location_lat=location.lat,
            location_lon=location.lon,
            moon_phase=moon_phase,
            rigs=rigs,
            locale=locale,
            sim_date=sim_date,
            compressed_objects=compressed_objects,
        )

        # Get AI response for ranking (increased timeout for large JSON response)
        ranking_response = get_ai_response(ranking_prompt["user"], system=ranking_prompt["system"], max_tokens=4000, timeout=300)

        # Parse ranking response to extract ranked objects
        # Expected format: JSON array with objects having "Object" key
        ranked_objects = []

        try:
            import json
            if not ranking_response:
                raise AIServiceError("AI returned an empty response")
            # Strip markdown code fences before parsing (handle various fence formats)
            ranking_response = ranking_response.strip()
            # Pattern: optional whitespace + ``` + optional language + newline ... content ... + newline + optional whitespace + ```
            # This handles: ```json\n[...]\n```, ```\n[...]\n```, ```js\n[...]\n```
            fence_pattern = r'^\s*```(?:json|js|javascript)?\s*\n([\s\S]*?)\n\s*```\s*$'
            match = re.match(fence_pattern, ranking_response, re.DOTALL | re.MULTILINE)
            if match:
                ranking_response = match.group(1).strip()
                logger.info("Stripped markdown code fences from AI response")
            # Also handle case where only opening fence exists (truncated response)
            elif ranking_response.startswith("```"):
                ranking_response = re.sub(r'^\s*```(?:json|js|javascript)?\s*\n?', '', ranking_response)
                ranking_response = ranking_response.strip()
                logger.warning("AI response had opening code fence but no closing fence (possibly truncated)")

            # Log the cleaned response for debugging (first 500 chars)
            logger.debug(f"Cleaned AI response (first 500 chars): '{ranking_response[:500]}'")

            # Try to parse as JSON first
            parsed = json.loads(ranking_response)

            if isinstance(parsed, list):
                ranked_objects = parsed
            elif isinstance(parsed, dict) and "ranked_objects" in parsed:
                ranked_objects = parsed["ranked_objects"]
            elif isinstance(parsed, dict) and "objects" in parsed:
                ranked_objects = parsed["objects"]
            else:
                # Fallback: try to extract object names from text
                ranked_objects = _extract_objects_from_text(ranking_response, objects_for_prompt)
        except json.JSONDecodeError:
            # Not JSON, try to extract from text
            logger.warning(f"Raw AI response was: '{ranking_response[:500]}'")
            ranked_objects = _extract_objects_from_text(ranking_response, objects_for_prompt)

        # Ensure all ranked objects have rank, reason, and recommended_rigs array
        for i, obj in enumerate(ranked_objects, 1):
            obj["rank"] = i
            if "reason" not in obj:
                obj["reason"] = ""

            # Normalize recommended_rigs to array (handle both array and string fallback)
            if "recommended_rigs" in obj:
                # Already an array - ensure it's a list
                if not isinstance(obj["recommended_rigs"], list):
                    obj["recommended_rigs"] = [obj["recommended_rigs"]]
            elif "recommended_rig" in obj:
                # Fallback from old format - convert to array
                rig_value = obj["recommended_rig"]
                if rig_value:
                    obj["recommended_rigs"] = [rig_value]
                else:
                    obj["recommended_rigs"] = []
                # Remove old key for consistency
                del obj["recommended_rig"]
            else:
                # No recommended rig provided
                obj["recommended_rigs"] = []

        # Return results with debug keys for inspection
        return jsonify({
            "ranked_objects": ranked_objects,
            "debug_filter_counts": counts,
            "debug_post_trim_count": len(objects_for_prompt),
            "debug_objects_sent_to_ai": len(objects_for_prompt),
            "debug_prompt_object_block": compressed_objects,
        })

    except AIServiceError as e:
        logger.error(f"AIServiceError in /api/ai/best_objects: {str(e)}")
        return jsonify({"error": str(e)}), 503

    except Exception as e:
        logger.exception("Unexpected error generating best objects")
        return jsonify({"error": "An unexpected error occurred"}), 500


def _extract_objects_from_text(text, objects_for_prompt):
    """Extract ranked objects from AI text response when JSON parsing fails.

    Args:
        text: AI response text
        objects_for_prompt: List of original objects for reference

    Returns:
        List of dicts with Object, rank, reason, recommended_rigs keys
    """
    import re

    # Create a map of object names to original object data
    obj_map = {o.get("Object", ""): o for o in objects_for_prompt}

    # Try to find object names in text (common patterns: M31, NGC 7000, etc.)
    # Look for object names that appear in our input list
    ranked_objects = []

    # Extract object names from text - look for patterns like "1. M31 - reason" or "1) M31: reason"
    pattern = r'(?:^|\n)\s*(\d+)[\.\)]\s*([A-Z]?[A-Za-z0-9\s\-]+)\s*[:-]\s*(.*?)(?=\n\d+[\.\)]|\n\n|$)'
    matches = re.findall(pattern, text, re.MULTILINE)

    for match in matches:
        rank_str, obj_name, reason = match
        obj_name = obj_name.strip()

        # Clean up the object name
        obj_name = re.sub(r'\s+', ' ', obj_name).strip()

        if obj_name in obj_map:
            ranked_objects.append({
                "Object": obj_name,
                "rank": int(rank_str),
                "reason": reason.strip(),
                "recommended_rigs": []
            })

    # If no matches found, return empty list
    if not ranked_objects:
        logger.warning("Could not parse AI response as structured data")

    return ranked_objects


@ai_bp.route("/api/ai/prefilter_debug", methods=["GET"])
def prefilter_debug():
    """Debug endpoint for testing pre-filter function against real object catalog.

    Returns filter counts and filtered object details for debugging.

    Returns:
        JSON: {
            "counts": {...stage breakdown... },
            "moon_phase": ...,
            "user_settings": ...,
            "max_aperture_mm": ...,
            "surviving_objects": [...]
        }
    """
    # Get user settings
    user_settings = {
        "min_observable_minutes": 60,
        "min_max_altitude": 30,
    }
    imaging_criteria = getattr(g, "user_config", {}).get("imaging_criteria", {})
    if imaging_criteria:
        user_settings["min_observable_minutes"] = imaging_criteria.get("min_observable_minutes", 60)
        user_settings["min_max_altitude"] = imaging_criteria.get("min_max_altitude", 30)

    # Get moon phase (use 11 PM tonight)
    moon_phase = 0
    local_date_str = None
    try:
        local_tz = pytz.timezone("UTC")
        now_local = datetime.now(local_tz)
        # If it's before noon, use "night of" previous day
        if now_local.hour < 12:
            date_obj = now_local.date() - timedelta(days=1)
        else:
            date_obj = now_local.date()
        local_date_str = date_obj.strftime("%Y-%m-%d")
        time_11pm_local = local_tz.localize(
            datetime.combine(date_obj, time(23, 0))
        )
        dt_utc = time_11pm_local.astimezone(pytz.utc)
        moon_phase = round(ephem.Moon(dt_utc).phase, 1)
    except Exception as e:
        logger.warning(f"Failed to calculate moon phase for debug: {e}")
        # Fallback to today
        local_date_str = datetime.now().strftime("%Y-%m-%d")

    # Get user's rigs to find max aperture
    db = get_db()
    rig_rows = db.query(Rig).options(
        selectinload(Rig.telescope)
    ).filter_by(user_id=g.db_user.id).all()

    max_aperture_mm = None
    for rig in rig_rows:
        if rig.telescope and rig.telescope.aperture_mm:
            if max_aperture_mm is None or rig.telescope.aperture_mm > max_aperture_mm:
                max_aperture_mm = rig.telescope.aperture_mm

    # Get user's default location for calculations
    location = db.query(Location).filter_by(
        user_id=g.db_user.id, is_default=True
    ).first()
    if not location:
        location = db.query(Location).filter_by(
            user_id=g.db_user.id
        ).first()

    # Get all enabled objects from DB for this user
    obj_records = db.query(AstroObject).filter_by(
        user_id=g.db_user.id
    ).all()

    # Import calculation functions
    from modules.astro_calculations import (
        calculate_transit_time,
        calculate_observable_duration_vectorized,
    )

    # Build object list with computed fields
    all_objects = []
    for obj_record in obj_records:
        try:
            if not location:
                # Skip if no location available
                continue

            ra = obj_record.ra_hours
            dec = obj_record.dec_deg
            lat = location.lat
            lon = location.lon
            tz_name = location.timezone or "UTC"
            altitude_threshold = location.altitude_threshold or 20

            # Calculate observable duration and max altitude
            obs_duration, max_altitude, _, _ = calculate_observable_duration_vectorized(
                ra, dec, lat, lon, local_date_str, tz_name, altitude_threshold
            )

            # Calculate transit time (for 11 PM altitude reference)
            transit_time = calculate_transit_time(
                ra, dec, lat, lon, tz_name, local_date_str
            )

            # Calculate moon separation at 11 PM
            angular_sep = None
            try:
                # Parse transit time for datetime conversion
                if transit_time and transit_time != "N/A":
                    time_11pm_local = pytz.timezone(tz_name).localize(
                        datetime.combine(
                            datetime.strptime(local_date_str, "%Y-%m-%d"),
                            datetime.strptime(transit_time, "%H:%M").time()
                        )
                    )
                    dt_11pm_utc = time_11pm_local.astimezone(pytz.utc)
                    time_obj = Time(dt_11pm_utc)
                    loc_obj = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
                    obj_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
                    frame = AltAz(obstime=time_obj, location=loc_obj)
                    moon_coord = get_body("moon", time_obj, loc_obj)
                    angular_sep = round(obj_coord.transform_to(frame).separation(
                        moon_coord.transform_to(frame)
                    ).deg, 1)
            except Exception as e:
                logger.debug(f"Failed to calculate moon separation for {obj_record.object_name}: {e}")

            obs_duration_min = int(obs_duration.total_seconds() / 60) if obs_duration else 0
            max_altitude_val = round(max_altitude, 1) if max_altitude is not None else 0

            all_objects.append({
                "Object": obj_record.object_name,
                "enabled": obj_record.enabled,
                "Magnitude": obj_record.magnitude,
                "Size": obj_record.size,
                "Type": obj_record.type,
                "Constellation": obj_record.constellation,
                "Observable Duration (min)": obs_duration_min,
                "Max Altitude (°)": max_altitude_val,
                "Angular Separation (°)": angular_sep,
            })
        except Exception as e:
            logger.debug(f"Failed to calculate data for {obj_record.object_name}: {e}")
            continue

    # Run pre-filter
    filtered_objects, counts = pre_filter_objects(
        all_objects,
        user_settings,
        moon_phase,
        max_aperture_mm
    )

    # Build surviving objects list with key values
    surviving_objects = [
        {
            "name": o["Object"],
            "type": o.get("Type"),
            "obs_duration": o.get("Observable Duration (min)"),
            "max_altitude": o.get("Max Altitude (°)"),
            "moon_separation": o.get("Angular Separation (°)"),
            "magnitude": o.get("Magnitude"),
            "size": o.get("Size"),
        }
        for o in filtered_objects
    ]

    return jsonify({
        "counts": counts,
        "moon_phase": moon_phase,
        "user_settings": user_settings,
        "max_aperture_mm": max_aperture_mm,
        "surviving_objects": surviving_objects,
        "total_catalog_size": len(all_objects),
    })


def register_ai_blueprint(app):
    """Register the AI blueprint only if AI_API_KEY is configured.

    Args:
        app: Flask application instance
    """
    api_key = app.config.get("AI_API_KEY")

    if api_key:
        app.register_blueprint(ai_bp)
