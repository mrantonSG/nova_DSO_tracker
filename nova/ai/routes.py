"""AI blueprint routes.

Flask blueprint for AI-related API endpoints. Only registers if AI_API_KEY
is present in app.config.
"""

import logging

from flask import Blueprint, jsonify, g, request

from nova.ai.config import ai_enabled, user_has_ai_access
from nova.ai.prompts import build_dso_notes_prompt
from nova.ai.service import get_ai_response, AIServiceError
from nova.helpers import get_db
from nova.models import AstroObject

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

    try:
        # Build the prompt
        prompt = build_dso_notes_prompt(object_data, locale=locale)

        # Get AI response
        notes = get_ai_response(prompt["user"], system=prompt["system"])

        return jsonify({"notes": notes})

    except AIServiceError as e:
        logger.error(f"AIServiceError in /api/ai/notes: {str(e)}")
        return jsonify({"error": str(e)}), 503

    except Exception as e:
        logger.exception("Unexpected error generating DSO notes")
        return jsonify({"error": "An unexpected error occurred"}), 500


def register_ai_blueprint(app):
    """Register the AI blueprint only if AI_API_KEY is configured.

    Args:
        app: Flask application instance
    """
    api_key = app.config.get("AI_API_KEY")

    if api_key:
        app.register_blueprint(ai_bp)
