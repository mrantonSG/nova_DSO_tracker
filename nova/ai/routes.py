"""AI blueprint routes.

Flask blueprint for AI-related API endpoints. Only registers if AI_API_KEY
is present in app.config.
"""

import logging

from flask import Blueprint, current_app, jsonify, g, request
from sqlalchemy.orm import selectinload

from nova.ai.config import ai_enabled, user_has_ai_access
from nova.ai.prompts import build_dso_notes_prompt, build_session_summary_prompt
from nova.ai.service import get_ai_response, AIServiceError
from nova.helpers import get_db
from nova.models import AstroObject, Location, Rig, JournalSession

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
            "is_default": loc.is_default,
            "altitude_threshold": loc.altitude_threshold,
        }
        for loc in loc_rows
    ]

    active_location = next(
        (l for l in locations if l["is_default"]),
        locations[0] if locations else None
    )

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

    try:
        # Build the prompt
        prompt = build_dso_notes_prompt(
            object_data,
            locations=locations,
            active_location=active_location,
            rigs=rigs,
            locale=locale
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
    """Generate AI-assisted summary for a journal session.

    Request body:
        JSON: {"session_id": int}

    Returns:
        JSON: {"summary": str} on success
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

            # Extract PHD2 stats
            phd2 = cached.get("phd2")
            if phd2 and phd2.get("stats"):
                log_analysis_summary["phd2_stats"] = phd2["stats"]

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
    import re
    notes = session_data.get("general_notes_problems_learnings")
    if notes:
        # Remove HTML tags but preserve content
        session_data["general_notes_problems_learnings"] = re.sub(r'<[^>]+>', ' ', notes).strip()

    # Get current locale using lazy import to avoid circular dependency
    from nova import get_locale
    locale = get_locale()

    try:
        # Build the prompt
        prompt = build_session_summary_prompt(session_data, locale=locale)

        # Get AI response
        summary = get_ai_response(prompt["user"], system=prompt["system"])

        # Convert plain text to HTML paragraphs for Trix editor
        summary = summary.strip()
        raw_paragraphs = [p.strip() for p in re.split(r'\n+', summary) if p.strip()]
        html_parts = []
        for p in raw_paragraphs:
            if p.startswith('<p>'):
                html_parts.append(p)
            else:
                html_parts.append(f'<p>{p}</p>')
        summary = ''.join(html_parts)

        return jsonify({"summary": summary})

    except AIServiceError as e:
        logger.error(f"AIServiceError in /api/ai/session-summary: {str(e)}")
        return jsonify({"error": str(e)}), 503

    except Exception as e:
        logger.exception("Unexpected error generating session summary")
        return jsonify({"error": "An unexpected error occurred"}), 500


def register_ai_blueprint(app):
    """Register the AI blueprint only if AI_API_KEY is configured.

    Args:
        app: Flask application instance
    """
    api_key = app.config.get("AI_API_KEY")

    if api_key:
        app.register_blueprint(ai_bp)
