"""
API key authentication for Nova DSO Tracker.

Supports two modes:
- Multi-user: each user manages their own API keys
- Single-user: a key is auto-generated on first startup and printed to console
"""

import hashlib
import secrets
from datetime import datetime
from functools import wraps

from flask import request, jsonify, g, current_app
from flask_login import current_user
from sqlalchemy.orm import selectinload

from nova.models import SessionLocal, ApiKey, DbUser
from nova.config import SINGLE_USER_MODE


def generate_api_key() -> str:
    """Generate a cryptographically secure API key.

    Format: nova_<48 random hex chars>  (total 53 chars)
    """
    return f"nova_{secrets.token_hex(24)}"


def hash_api_key(raw_key: str) -> str:
    """Hash an API key for storage using SHA-256."""
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def key_prefix(raw_key: str) -> str:
    """Return a short prefix for display (e.g. 'nova_a3f2...')."""
    return raw_key[:12]


def create_api_key(db, user_id: int, name: str = "default") -> str:
    """Create a new API key for a user.  Returns the raw key (only chance to see it)."""
    raw_key = generate_api_key()
    api_key = ApiKey(
        key_hash=hash_api_key(raw_key),
        key_prefix=key_prefix(raw_key),
        user_id=user_id,
        name=name,
        is_active=True,
    )
    db.add(api_key)
    db.commit()
    return raw_key


def verify_api_key(raw_key: str):
    """Look up an API key and return the (ApiKey, DbUser) or (None, None).

    The DbUser is returned with roles eagerly loaded to avoid DetachedInstanceError
    when checking permissions after the session is removed.
    """
    hashed = hash_api_key(raw_key)
    db = SessionLocal()
    try:
        api_key = (
            db.query(ApiKey)
            .filter(
                ApiKey.key_hash == hashed,
                ApiKey.is_active.is_(True),
            )
            .first()
        )
        if api_key is None:
            return None, None
        # Eagerly load roles to avoid DetachedInstanceError when checking permissions
        db_user = (
            db.query(DbUser)
            .options(selectinload(DbUser.roles))
            .filter(DbUser.id == api_key.user_id)
            .first()
        )
        if db_user is None:
            return None, None
        # Update last-used timestamp (fire-and-forget)
        api_key.last_used_at = datetime.utcnow()
        db.commit()
        return api_key, db_user
    finally:
        SessionLocal.remove()


def _extract_api_key_from_request():
    """Extract API key from request headers.

    Supports:
      - X-API-Key: <key>
      - Authorization: Bearer <key>
    """
    # Check X-API-Key header first
    api_key = request.headers.get("X-API-Key")
    if api_key:
        return api_key

    # Check Authorization: Bearer <key>
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()

    return None


def api_key_required(f):
    """Decorator: require a valid API key for the endpoint.

    On success, sets:
      - g.api_key_obj   (the ApiKey row)
      - g.db_user       (the DbUser row)
      - g.api_username  (username string)
    """

    @wraps(f)
    def decorated(*args, **kwargs):
        raw_key = _extract_api_key_from_request()
        if raw_key is None:
            return jsonify(
                {
                    "error": "Missing API key. Provide X-API-Key header or Authorization: Bearer <key>."
                }
            ), 401

        api_key_obj, db_user = verify_api_key(raw_key)
        if api_key_obj is None:
            return jsonify({"error": "Invalid or revoked API key."}), 401

        g.api_key_obj = api_key_obj
        g.db_user = db_user
        g.api_username = db_user.username
        return f(*args, **kwargs)

    return decorated


def api_key_or_login_required(f):
    """Decorator: accept EITHER a valid API key OR an active Flask-Login session.

    Use this on existing endpoints that should support both browser and
    programmatic access.
    """

    @wraps(f)
    def decorated(*args, **kwargs):
        raw_key = _extract_api_key_from_request()

        if raw_key is not None:
            # API-key path
            api_key_obj, db_user = verify_api_key(raw_key)
            if api_key_obj is None:
                return jsonify({"error": "Invalid or revoked API key."}), 401
            g.api_key_obj = api_key_obj
            g.db_user = db_user
            g.api_username = db_user.username
            return f(*args, **kwargs)

        # Fall through to session auth
        if current_user.is_authenticated:
            return f(*args, **kwargs)

        return jsonify(
            {"error": "Authentication required. Provide an API key or log in."}
        ), 401

    return decorated


def ensure_single_user_api_key(app):
    """In single-user mode, ensure an API key exists and always print it.

    Called once during app startup.  If a key already exists for the
    default user it is rotated so the raw key can be displayed.
    """
    if not SINGLE_USER_MODE:
        return

    from nova.helpers import get_db

    db = SessionLocal()
    try:
        # Make sure the default DbUser exists
        db_user = db.query(DbUser).filter(DbUser.username == "default").first()
        if db_user is None:
            db_user = DbUser(username="default", active=True)
            db.add(db_user)
            db.commit()

        # Check for existing key
        existing = (
            db.query(ApiKey)
            .filter(
                ApiKey.user_id == db_user.id,
                ApiKey.is_active.is_(True),
            )
            .first()
        )

        if existing is not None:
            # Rotate: delete old key and create a fresh one so we can print it
            db.delete(existing)
            db.commit()

        raw_key = create_api_key(db, db_user.id, name="auto-generated")
        print("\n" + "=" * 60)
        print("  NOVA DSO TRACKER — API KEY (single-user mode)")
        print("=" * 60)
        print(f"  {raw_key}")
        print("=" * 60)
        print("  Save this key — it will NOT be shown again.")
        print("  Use it via:  X-API-Key: <key>")
        print("            or Authorization: Bearer <key>")
        print("=" * 60 + "\n")
        app.logger.info(f"Generated new API key (prefix: {key_prefix(raw_key)}...)")
    finally:
        SessionLocal.remove()
