"""
Authentication infrastructure for Nova DSO Tracker.

Conditional setup for single-user vs multi-user mode:
- Multi-user: Flask-SQLAlchemy ``db`` + ORM-backed User model (users.db)
- Single-user: lightweight UserMixin stub (no database)

Call ``init_auth(app)`` once from the app factory to bind everything to the
Flask application.
"""

import os

from flask_login import LoginManager, UserMixin  # noqa: F401 — re-exported for test compatibility
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text
from werkzeug.security import generate_password_hash, check_password_hash

from nova.config import SINGLE_USER_MODE
from nova.models import INSTANCE_PATH

login_manager = LoginManager()

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
                db.session.execute(text("SELECT 1 FROM user LIMIT 1"))
            except Exception:
                try:
                    print("[MIGRATION] User table missing. Creating all tables...")
                    db.create_all()
                    print("✅ [MIGRATION] Database initialized.")
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
    - Multi-user: only accept integer IDs; stale values → None
    """
    if SINGLE_USER_MODE:
        return User(user_id="default", username="default") if user_id == "default" else None

    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return None
    return db.session.get(User, uid)
