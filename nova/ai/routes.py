"""AI blueprint routes.

Flask blueprint for AI-related API endpoints. Only registers if AI_API_KEY
is present in app.config.
"""

import logging
import re
from datetime import datetime, time

import ephem
import pytz
from astropy.coordinates import EarthLocation, AltAz, SkyCoord, get_body
from astropy.time import Time
import astropy.units as u

from flask import Blueprint, current_app, jsonify, g, request, Response, stream_with_context
from sqlalchemy.orm import selectinload

from nova.ai.config import ai_enabled, user_has_ai_access
from nova.ai.prompts import build_dso_notes_prompt, build_session_summary_prompt
from nova.ai.service import get_ai_response, AIServiceError
from nova.helpers import get_db
from nova.models import AstroObject, Location, Rig, JournalSession, SavedFraming

logger = logging.getLogger(__name__)

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

            # Calculate target altitude AT transit time (this is the max observable altitude)
            # Parse transit time (format: "HH:MM")
            transit_hour, transit_minute = map(int, target_transit_time.split(':'))
            time_transit_local = local_tz.localize(datetime.combine(date_obj, time(transit_hour, transit_minute)))
            dt_transit_utc = time_transit_local.astimezone(pytz.utc)

            # Reuse object coordinates, calculate new frame at transit time
            time_obj_transit = Time(dt_transit_utc)
            loc_obj_transit = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
            frame_transit = AltAz(obstime=time_obj_transit, location=loc_obj_transit)
            obj_altaz_transit = obj_coord_sep.transform_to(frame_transit)

            target_altitude_deg = round(obj_altaz_transit.alt.deg, 1)
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
        notes = get_ai_response(prompt["user"], system=prompt["system"])

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

            for chunk in get_ai_response(prompt["user"], system=prompt["system"], stream=True):
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


def register_ai_blueprint(app):
    """Register the AI blueprint only if AI_API_KEY is configured.

    Args:
        app: Flask application instance
    """
    api_key = app.config.get("AI_API_KEY")

    if api_key:
        app.register_blueprint(ai_bp)
