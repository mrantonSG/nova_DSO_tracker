"""AI configuration helpers.

Reads AI-related settings from Flask's app.config (which loads from instance/.env).
"""

from flask import current_app


def ai_enabled() -> bool:
    """Check if AI is globally enabled (API key is configured)."""
    api_key = current_app.config.get("AI_API_KEY")
    return bool(api_key)


def user_has_ai_access(username: str) -> bool:
    """Check if a specific user has AI access.

    Reads AI_ALLOWED_USERS from app.config (comma-separated usernames).
    If AI_ALLOWED_USERS is empty or missing, no user has access even if key is present.
    """
    allowed_users = current_app.config.get("AI_ALLOWED_USERS", "")

    if not allowed_users:
        return False

    # Parse comma-separated list, strip whitespace
    allowed_list = [u.strip() for u in allowed_users.split(",") if u.strip()]

    return username in allowed_list
