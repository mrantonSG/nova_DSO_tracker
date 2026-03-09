"""
Nova DSO Tracker - Core Blueprint
----------------------------------
Auth & utility routes: login, logout, SSO, language setting, favicon, uploaded images.
"""

# =============================================================================
# Standard Library Imports
# =============================================================================
import json
import os

# =============================================================================
# Third-Party Imports
# =============================================================================
import jwt
from flask import (
    Blueprint, current_app, flash, g, redirect, render_template, request, session,
    send_from_directory, url_for
)
from flask_babel import gettext as _
from flask_login import current_user, login_required, login_user, logout_user

# =============================================================================
# Nova Package Imports (no circular import)
# =============================================================================
from nova import SINGLE_USER_MODE  # Import from nova for test patching compatibility
from nova.analytics import record_login
from nova.config import UPLOAD_FOLDER
from nova.helpers import get_db
from nova.models import UiPref
# Note: User is defined conditionally in nova/__init__.py, lazy-imported where needed


# =============================================================================
# Blueprint Definition
# =============================================================================
core_bp = Blueprint('core', __name__)


# =============================================================================
# Auth & Utility Routes
# =============================================================================

@core_bp.route('/logout', methods=['POST'])
def logout():
    logout_user()
    session.clear()  # Optional: reset session if needed
    flash(_("Logged out successfully!"), "success")
    return redirect(url_for('core.login'))


@core_bp.route('/set_language/<lang>')
def set_language(lang):
    """Set the user's preferred language and redirect back."""
    # Validate the language is supported
    supported_locales = current_app.config.get('BABEL_SUPPORTED_LOCALES', ['en'])
    if lang not in supported_locales:
        flash(_("Language '%(lang)s' is not supported.", lang=lang), "error")
        return redirect(request.referrer or url_for('core.index'))

    # Get the current user
    if not hasattr(g, 'db_user') or not g.db_user:
        # For guest users, just set session and redirect
        session['language'] = lang
        return redirect(request.referrer or url_for('core.index'))

    # Save to UiPref.json_blob for authenticated users
    db = get_db()
    try:
        prefs = db.query(UiPref).filter_by(user_id=g.db_user.id).first()
        if not prefs:
            prefs = UiPref(user_id=g.db_user.id, json_blob='{}')
            db.add(prefs)

        # Load existing settings, add language, save back
        try:
            settings = json.loads(prefs.json_blob or '{}')
        except json.JSONDecodeError:
            settings = {}

        settings['language'] = lang
        prefs.json_blob = json.dumps(settings, ensure_ascii=False)
        db.commit()

        # Update g.user_config for current request
        if hasattr(g, 'user_config'):
            g.user_config['language'] = lang

    except Exception as e:
        db.rollback()
        print(f"[SET_LANGUAGE] Error saving language preference: {e}")

    # Redirect back to the previous page
    return redirect(request.referrer or url_for('core.index'))


@core_bp.route('/login', methods=['GET', 'POST'])
def login():
    if SINGLE_USER_MODE:
        # In single-user mode, the login page is not needed, just redirect.
        return redirect(url_for('core.index'))
    else:
        # --- MULTI-USER MODE LOGIC ---
        from nova import db, User  # Lazy import: db and User only exist in multi-user mode

        if request.method == 'POST':
            username = request.form.get('username')
            password = request.form.get('password')
            user = db.session.scalar(db.select(User).where(User.username == username))
            if user and user.check_password(password):
                login_user(user)
                record_login()
                session.modified = True  # Force session save before redirect
                flash(_("Logged in successfully!"), "success")

                # --- START: THIS IS THE CORRECTED LOGIC ---
                # Read 'next' from the form's hidden input, not the URL
                next_page = request.form.get('next')

                # Security check: Only redirect if 'next' is a relative path
                # Use 303 redirect to ensure browser does a fresh GET with the new session cookie
                if next_page and next_page.startswith('/'):
                    return redirect(next_page, code=303)

                # Default redirect if 'next' is missing or invalid
                return redirect(url_for('core.index'), code=303)
                # --- END OF CORRECTION ---

            else:
                flash(_("Invalid username or password."), "error")
        return render_template('login.html')


@core_bp.route('/sso/login')
def sso_login():
    # First, check if the app is in single-user mode. SSO is not applicable here.
    if SINGLE_USER_MODE:
        flash(_("Single Sign-On is not applicable in single-user mode."), "error")
        return redirect(url_for('core.index'))

    from nova import db, User  # Lazy import: db and User only exist in multi-user mode

    # Get the token from the URL (e.g., ?token=...)
    token = request.args.get('token')
    if not token:
        flash(_("SSO Error: No token provided."), "error")
        return redirect(url_for('core.login'))

    # Get the shared secret key from the .env file
    secret_key = os.environ.get('JWT_SECRET_KEY')
    if not secret_key:
        flash(_("SSO Error: SSO is not configured on the server."), "error")
        return redirect(url_for('core.login'))

    try:
        # Decode the token. This automatically verifies the signature and expiration.
        payload = jwt.decode(token, secret_key, algorithms=["HS256"])
        username = payload.get('username')

        if not username:
            raise jwt.InvalidTokenError("Token is missing username.")

        # Find the user in the Nova database
        user = db.session.scalar(db.select(User).where(User.username == username))

        if user and user.is_active:
            login_user(user)  # Log the user in using Flask-Login
            record_login()
            session.modified = True  # Force session save before redirect
            flash(_("Welcome back, %(username)s!", username=user.username), "success")
            return redirect(url_for('core.index'), code=303)
        else:
            flash(_("SSO Error: User '%(username)s' not found or is disabled in Nova.", username=username), "error")
            return redirect(url_for('core.login'))

    except jwt.ExpiredSignatureError:
        flash(_("SSO Error: The login link has expired. Please try again from WordPress."), "error")
        return redirect(url_for('core.login'))
    except jwt.InvalidTokenError:
        flash(_("SSO Error: Invalid login token."), "error")
        return redirect(url_for('core.login'))


@core_bp.route('/favicon.ico')
def favicon():
    return send_from_directory('static', 'favicon.ico', mimetype='image/vnd.microsoft.icon')


@core_bp.route('/uploads/<path:username>/<path:filename>')
@login_required
def get_uploaded_image(username, filename):
    """
    Serve uploaded images.

    Compatible with:
    - Legacy notes where Trix stored /uploads/<old_username>/...
    - Photo ZIP imports that always extract into the *current* user's directory
    - Single-user installs that store everything under 'default'
    """

    candidate_dirs = []

    # 1) Directory that matches the URL segment (legacy behaviour)
    candidate_dirs.append(os.path.join(UPLOAD_FOLDER, username))

    # 2) In multi-user mode, also try the current user's directory.
    #    This fixes MU→MU migrations where the username changed:
    #    old HTML: /uploads/mrantonsG/..., new files: uploads/anton/...
    if not SINGLE_USER_MODE:
        current_name = getattr(current_user, "username", None)
        if current_name and current_name != username:
            candidate_dirs.append(os.path.join(UPLOAD_FOLDER, current_name))

    # 3) In single-user mode, fall back to "default" for legacy paths.
    if SINGLE_USER_MODE and username != "default":
        candidate_dirs.append(os.path.join(UPLOAD_FOLDER, "default"))

    for user_upload_dir in candidate_dirs:
        base_dir = os.path.abspath(user_upload_dir)
        target_path = os.path.abspath(os.path.join(user_upload_dir, filename))

        # Prevent path traversal
        if not target_path.startswith(base_dir + os.sep):
            continue

        if os.path.exists(target_path):
            return send_from_directory(user_upload_dir, filename)

    return "Not Found", 404
