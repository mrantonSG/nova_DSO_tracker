"""
Authentication infrastructure for Nova DSO Tracker.

Conditional setup for single-user vs multi-user mode:
- Multi-user: Flask-SQLAlchemy ``db`` + ORM-backed User model (users.db)
- Single-user: lightweight UserMixin stub (no database)

Call ``init_auth(app)`` once from the app factory to bind everything to the
Flask application.
"""

import hashlib
import hmac
import os
import secrets

from flask_login import LoginManager, UserMixin  # noqa: F401 — re-exported for test compatibility
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash

from nova.config import SINGLE_USER_MODE, USER_ADMIN_USERNAME, USER_ADMIN_PASSWORD
from nova.models import INSTANCE_PATH

login_manager = LoginManager()


def _session_fingerprint(user_id, username, password_hash):
    """First 16 hex chars of sha256("id:username:password_hash")."""
    raw = f"{user_id}:{username}:{password_hash or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def session_id_for(user):
    """
    Flask-Login id for a users.db account: "<id>:<fingerprint>".
    The fingerprint changes when the account is re-created under a reused
    id, renamed, or given a new password, so old cookies stop loading.
    No part of password_hash is stored in the cookie.
    """
    return f"{user.id}:{_session_fingerprint(user.id, user.username, getattr(user, 'password_hash', None))}"

# ---------------------------------------------------------------------------
# Conditional db & User
# ---------------------------------------------------------------------------
if not SINGLE_USER_MODE:
    db = SQLAlchemy()  # un-bound; call init_auth(app) to bind to Flask app

    class User(UserMixin, db.Model):
        __tablename__ = 'user'
        id = db.Column(db.Integer, primary_key=True)
        username = db.Column(db.String(80), unique=True, nullable=False)
        password_hash = db.Column(db.String(256), nullable=False)
        active = db.Column(db.Boolean, nullable=False, default=True)

        def set_password(self, password):
            self.password_hash = generate_password_hash(password)

        def check_password(self, password):
            return check_password_hash(self.password_hash, password)

        def get_id(self):
            return session_id_for(self)

        @property
        def is_active(self):
            return bool(self.active)

else:
    db = None  # type: ignore[assignment]

    class User(UserMixin):  # type: ignore[no-redef]
        def __init__(self, user_id, username):
            self.id = user_id
            self.username = username


# ---------------------------------------------------------------------------
# App-binding (called from app factory)
# ---------------------------------------------------------------------------
def init_auth(app):
    """
    Bind auth infrastructure to a Flask app.  Called once from the app
    factory in nova/__init__.py.
    """
    login_manager.init_app(app)
    login_manager.login_view = 'core.login'

    if not SINGLE_USER_MODE:
        db_path = os.path.join(INSTANCE_PATH, 'users.db')
        app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
        app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
        db.init_app(app)

        # Ensure DB tables exist on first run / after switching modes
        with app.app_context():
            try:
                user_count = db.session.execute(
                    text("SELECT COUNT(*) FROM user")
                ).scalar()
                if user_count == 0:
                    try:
                        _pwd = USER_ADMIN_PASSWORD if USER_ADMIN_PASSWORD else secrets.token_urlsafe(16)
                        default_user = User(username=USER_ADMIN_USERNAME)
                        default_user.set_password(_pwd)
                        db.session.add(default_user)
                        db.session.commit()
                        if USER_ADMIN_PASSWORD:
                            print(f"[STARTUP] Admin user '{USER_ADMIN_USERNAME}' created from environment.")
                        else:
                            print(f"[AUTH] Generated admin password for '{USER_ADMIN_USERNAME}': {_pwd} (set USER_ADMIN_PASSWORD to choose your own)")
                    except IntegrityError:
                        db.session.rollback()
                        print("[STARTUP] Admin user already created by another worker. Skipping.")
            except Exception:
                try:
                    print("[MIGRATION] User table missing. Creating all tables...")
                    db.create_all()
                    print("✅ [MIGRATION] Database initialized.")
                    try:
                        _pwd = USER_ADMIN_PASSWORD if USER_ADMIN_PASSWORD else secrets.token_urlsafe(16)
                        default_user = User(username=USER_ADMIN_USERNAME)
                        default_user.set_password(_pwd)
                        db.session.add(default_user)
                        db.session.commit()
                        if USER_ADMIN_PASSWORD:
                            print(f"[STARTUP] Admin user '{USER_ADMIN_USERNAME}' created from environment.")
                        else:
                            print(f"[AUTH] Generated admin password for '{USER_ADMIN_USERNAME}': {_pwd} (set USER_ADMIN_PASSWORD to choose your own)")
                    except IntegrityError:
                        db.session.rollback()
                        print("[STARTUP] Admin user already created by another worker. Skipping.")
                except Exception as e:
                    print(f"❌ [MIGRATION] Failed to initialize DB: {e}")


# ---------------------------------------------------------------------------
# Unified user loader
# ---------------------------------------------------------------------------
@login_manager.user_loader
def load_user(user_id):
    """
    Unified loader:
    - SINGLE_USER_MODE: expect sentinel 'default'
    - Multi-user: expect "<id>:<fingerprint>" (see session_id_for); a wrong
      format (e.g. an old plain-id cookie) or a fingerprint that doesn't
      match the loaded account → None
    """
    if SINGLE_USER_MODE:
        return User(user_id="default", username="default") if user_id == "default" else None

    try:
        uid_str, fp = user_id.split(":", 1)
        uid = int(uid_str)
    except (TypeError, ValueError, AttributeError):
        return None
    user = db.session.get(User, uid)
    if user is None:
        return None
    expected = _session_fingerprint(user.id, user.username, user.password_hash)
    return user if hmac.compare_digest(fp, expected) else None
