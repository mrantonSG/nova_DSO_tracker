"""
Nova DSO Tracker - Core Blueprint
----------------------------------
Auth & utility routes: login, logout, SSO, language setting, favicon, uploaded images.
"""

# =============================================================================
# Standard Library Imports
# =============================================================================
import datetime
import json
import os
import subprocess
import sys
import threading
import traceback

# =============================================================================
# Third-Party Imports
# =============================================================================
import jwt
from flask import (
    Blueprint, current_app, flash, g, jsonify, redirect, render_template, request, session,
    send_from_directory, url_for
)
from flask_babel import gettext as _
from flask_login import current_user, login_required, login_user, logout_user

# =============================================================================
# Nova Package Imports (no circular import)
# =============================================================================
import bleach
import pytz
import yaml

from flask import abort, jsonify, redirect, Response, stream_with_context
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload
import numpy as np
import time
from astropy.coordinates import SkyCoord, get_constellation
import astropy.units as u
import ephem

from nova import SINGLE_USER_MODE  # Import from nova for test patching compatibility
from nova.analytics import record_event, record_login
from nova.config import CACHE_DIR, UPLOAD_FOLDER, cache_worker_status
from nova.helpers import (
    _parse_float_from_request,
    convert_to_native_python,
    get_db,
    get_ra_dec,
    get_user_log_string,
    load_full_astro_context,
    normalize_object_name,
)
from nova.models import (
    AnalyticsEvent,
    AnalyticsLogin,
    AstroObject,
    DbUser,
    HorizonPoint,
    JournalSession,
    Location,
    Project,
    SessionLocal,
    UiPref,
    UserCustomFilter,
)
from datetime import date, datetime, timedelta

from modules.astro_calculations import calculate_sun_events_cached
import requests
import modules.nova_data_fetcher as nova_data_fetcher

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


@core_bp.route('/set_location', methods=['POST'])
@login_required
def set_location_api():
    data = request.get_json()
    location_name = data.get("location", "").strip() if data.get("location") else ""

    user_id = g.db_user.id
    username = g.db_user.username
    db = get_db()

    # Validation: Query DB directly (don't rely on g.locations which may not be loaded)
    location = db.query(Location).filter_by(user_id=user_id, name=location_name).first()
    if not location:
        return jsonify({"status": "error", "message": "Location not found"}), 404

    try:
        # Step 1: Clear ALL is_default for this user
        db.query(Location).filter_by(user_id=user_id).update(
            {"is_default": False}, synchronize_session=False
        )

        # Step 2: Set the new default
        location.is_default = True

        # Step 3: Update JSON Preferences
        prefs = db.query(UiPref).filter_by(user_id=user_id).first()
        if not prefs:
            prefs = UiPref(user_id=user_id, json_blob='{}')
            db.add(prefs)

        try:
            settings = json.loads(prefs.json_blob or '{}')
        except json.JSONDecodeError:
            settings = {}

        settings['default_location'] = location_name
        prefs.json_blob = json.dumps(settings)

        db.commit()

        # Update in-memory global state
        if hasattr(g, 'user_config'):
            g.user_config['default_location'] = location_name
        g.selected_location = location_name

        return jsonify({"status": "success", "message": f"Location set to {location_name}"})

    except Exception as e:
        db.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500


@core_bp.route('/get_locations')
@login_required
def get_locations():
    """Returns only ACTIVE locations for the main UI dropdown and the user's default."""
    # Determine username based on mode and authentication status
    username = "default" if SINGLE_USER_MODE else (current_user.username if current_user.is_authenticated else "guest_user")
    db = get_db()
    try:
        # Find the user record in the application database
        user = db.query(DbUser).filter_by(username=username).one_or_none()
        if not user:
            # If the user doesn't exist in app.db, return empty lists
            return jsonify({"locations": [], "selected": None})

        # Query the database for locations belonging to this user that are marked as active
        active_locs = db.query(Location).filter_by(user_id=user.id, active=True).order_by(Location.name).all()
        # Extract just the names for the dropdown list
        active_loc_names = [loc.name for loc in active_locs]

        # Determine which location should be pre-selected in the dropdown
        selected = None
        # Find if any of the active locations is also marked as the default
        default_loc = next((loc.name for loc in active_locs if loc.is_default), None)

        if default_loc:
            # If an active default location exists, use it
            selected = default_loc
        elif active_loc_names:
            # Otherwise, if there are any active locations, use the first one in the list
            selected = active_loc_names[0]
        # If there are no active locations, 'selected' remains None

        # Return the list of active location names and the name of the location to be selected
        return jsonify({"locations": active_loc_names, "selected": selected})
    except Exception as e:
        # Log any unexpected errors during database access
        print(f"Error in get_locations for user '{username}': {e}")
        # Return an error response or an empty list in case of failure
        return jsonify({"locations": [], "selected": None, "error": str(e)}), 500


@core_bp.route('/proxy_focus', methods=['POST'])
@login_required
def proxy_focus():
    from nova import DEFAULT_HTTP_TIMEOUT, STELLARIUM_API_URL_BASE, STELLARIUM_ERROR_MESSAGE
    payload = request.form
    try:
        # This line ensures the dynamically determined STELLARIUM_API_URL_BASE is used:
        stellarium_focus_url = f"{STELLARIUM_API_URL_BASE}/api/main/focus"

        # print(f"[PROXY FOCUS] Attempting to connect to Stellarium at: {stellarium_focus_url}")  # For debugging

        # Make the request to Stellarium
        r = requests.post(stellarium_focus_url, data=payload, timeout=DEFAULT_HTTP_TIMEOUT)  # Added timeout
        r.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)

        # print(f"[PROXY FOCUS] Stellarium response: {r.status_code}")  # For debugging
        return jsonify({"status": "success", "stellarium_response": r.text})

    except requests.exceptions.ConnectionError:
        # Specific error if Stellarium isn't running or reachable at the URL
        message = f"Could not connect to Stellarium at {STELLARIUM_API_URL_BASE}. Ensure Stellarium is running, Remote Control is enabled, and the URL is correct."
        if STELLARIUM_ERROR_MESSAGE:  # User-defined message overrides if present
            message = STELLARIUM_ERROR_MESSAGE
        print(f"[PROXY FOCUS ERROR] ConnectionError: {message}")
        return jsonify({"status": "error", "message": message}), 503  # 503 Service Unavailable

    except requests.exceptions.Timeout:
        # Specific error for timeouts
        message = f"Connection to Stellarium at {STELLARIUM_API_URL_BASE} timed out after 10 seconds."
        print(f"[PROXY FOCUS ERROR] Timeout: {message}")
        return jsonify({"status": "error", "message": message}), 504  # 504 Gateway Timeout

    except requests.exceptions.HTTPError as http_err:
        # Specific error for HTTP errors from Stellarium (e.g., API errors)
        error_details = http_err.response.text if http_err.response is not None else "No response details"
        message = f"Stellarium at {STELLARIUM_API_URL_BASE} returned an error: {http_err}. Details: {error_details}"
        status_code = http_err.response.status_code if http_err.response is not None else 500
        print(f"[PROXY FOCUS ERROR] HTTPError {status_code}: {message}")
        return jsonify({"status": "error", "message": message}), status_code

    except Exception as e:
        # Catch-all for other unexpected errors
        message = STELLARIUM_ERROR_MESSAGE or f"An unexpected error occurred while attempting to contact Stellarium: {str(e)}"
        print(f"[PROXY FOCUS ERROR] Unexpected error: {e}")  # Log the actual error
        return jsonify({"status": "error", "message": message}), 500


@core_bp.route('/config_form', methods=['GET', 'POST'])
@login_required
def config_form():
    import traceback
    from nova import discover_catalog_packs

    load_full_astro_context()
    error = None
    message = None
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        app_db_user = db.query(DbUser).filter_by(username=username).one_or_none()
        if not app_db_user:
            flash(_("Could not find user '%(username)s' in the database.", username=username), "error")
            return redirect(url_for('core.index'))

        if request.method == 'POST':
            # --- General Settings Tab ---
            if 'submit_general' in request.form:
                prefs = db.query(UiPref).filter_by(user_id=app_db_user.id).first()
                if not prefs:
                    prefs = UiPref(user_id=app_db_user.id, json_blob='{}')
                    db.add(prefs)
                try:
                    settings = json.loads(prefs.json_blob or '{}')
                except json.JSONDecodeError:
                    settings = {}
                settings['altitude_threshold'] = int(request.form.get('altitude_threshold', 20))
                settings['default_location'] = request.form.get('default_location', settings.get('default_location'))
                # Settings available to ALL users
                settings['calc_invisible'] = bool(request.form.get('calc_invisible'))
                settings['hide_invisible'] = bool(request.form.get('hide_invisible'))
                # Theme preference: 'follow_system', 'always_light', 'always_dark'
                theme_value = request.form.get('theme_preference', 'follow_system')
                if theme_value in ('follow_system', 'always_light', 'always_dark'):
                    settings['theme_preference'] = theme_value

                if SINGLE_USER_MODE:
                    settings['sampling_interval_minutes'] = int(request.form.get("sampling_interval", 15))
                    settings.setdefault('telemetry', {})['enabled'] = bool(request.form.get('telemetry_enabled'))

                imaging_criteria = settings.setdefault("imaging_criteria", {})
                imaging_criteria["min_observable_minutes"] = int(request.form.get("min_observable_minutes", 60))
                imaging_criteria["min_max_altitude"] = int(request.form.get("min_max_altitude", 30))
                imaging_criteria["max_moon_illumination"] = int(request.form.get("max_moon_illumination", 20))
                imaging_criteria["min_angular_separation"] = int(request.form.get("min_angular_separation", 30))
                imaging_criteria["search_horizon_months"] = int(request.form.get("search_horizon_months", 6))
                prefs.json_blob = json.dumps(settings)
                message = "General settings updated."

            # --- Add New Location ---
            elif 'submit_new_location' in request.form:
                new_name = request.form.get("new_location").strip()
                new_tz = request.form.get("new_timezone")  # Get the timezone

                existing = db.query(Location).filter_by(user_id=app_db_user.id, name=new_name).first()
                if existing:
                    error = f"A location named '{new_name}' already exists."
                elif not all([new_name, request.form.get("new_lat"), request.form.get("new_lon"), new_tz]):
                    error = "Name, Latitude, Longitude, and Timezone are required."

                elif new_tz not in pytz.all_timezones:
                    error = f"Invalid timezone: '{new_tz}'. Please select a valid option from the list."

                else:
                    new_loc = Location(
                        user_id=app_db_user.id, name=new_name,
                        lat=float(request.form.get("new_lat")), lon=float(request.form.get("new_lon")),
                        timezone=request.form.get("new_timezone"), active=request.form.get("new_active") == "on",
                        comments=request.form.get("new_comments", "").strip()[:500]
                    )
                    db.add(new_loc);
                    db.flush()
                    mask_str = request.form.get("new_horizon_mask", "").strip()
                    if mask_str:
                        try:
                            mask_data = yaml.safe_load(mask_str)
                            if isinstance(mask_data, list):
                                for point in mask_data:
                                    db.add(HorizonPoint(location_id=new_loc.id, az_deg=float(point[0]),
                                                        alt_min_deg=float(point[1])))
                        except (yaml.YAMLError, ValueError, TypeError):
                            flash(_("Warning: Horizon Mask was invalid and was ignored."), "warning")
                    message = "New location added."

            # --- Update Existing Locations ---
            elif 'submit_locations' in request.form:
                locs_to_update = db.query(Location).filter_by(user_id=app_db_user.id).all()
                total_locs = len(locs_to_update)

                # Guard: Count locations marked for deletion and check active status after update
                locs_marked_for_deletion = 0
                active_locations_after_update = 0
                for loc in locs_to_update:
                    if request.form.get(f"delete_loc_{loc.name}") == "on":
                        locs_marked_for_deletion += 1
                    else:
                        # This location survives - check if it will be active
                        will_be_active = request.form.get(f"active_{loc.name}") == "on"
                        if will_be_active:
                            active_locations_after_update += 1

                # Prevent deleting the last location
                if locs_marked_for_deletion >= total_locs:
                    flash(_("Cannot delete your last location. You must have at least one location configured."), "error")
                    return redirect(url_for('core.config_form'))

                # Prevent having zero active locations
                if active_locations_after_update == 0:
                    flash(_("Cannot deactivate your only active location. You must keep at least one active location."), "error")
                    return redirect(url_for('core.config_form'))

                # Safe to proceed with deletions and updates
                for loc in locs_to_update:
                    if request.form.get(f"delete_loc_{loc.name}") == "on":
                        db.delete(loc);
                        continue

                    tz_name_from_form = request.form.get(f"timezone_{loc.name}")
                    if tz_name_from_form not in pytz.all_timezones:
                        error = f"Invalid timezone for {loc.name}: '{tz_name_from_form}'. Please select a valid option."
                        break  # Stop processing immediately on the first error

                    loc.lat = float(request.form.get(f"lat_{loc.name}"))
                    loc.lon = float(request.form.get(f"lon_{loc.name}"))
                    loc.timezone = request.form.get(f"timezone_{loc.name}")
                    loc.active = request.form.get(f"active_{loc.name}") == "on"
                    loc.comments = request.form.get(f"comments_{loc.name}", "").strip()[:500]

                    # --- START FIX: Use relationship assignment for cascade ---
                    # 1. Create a new, empty list for this location's points.
                    new_horizon_points = []
                    mask_str = request.form.get(f"horizon_mask_{loc.name}", "").strip()

                    if mask_str:
                        try:
                            mask_data = yaml.safe_load(mask_str)
                            if isinstance(mask_data, list):
                                # 2. Create new HorizonPoint objects and add them to the new list.
                                for point in mask_data:
                                    new_horizon_points.append(
                                        HorizonPoint(location_id=loc.id, az_deg=float(point[0]),
                                                     alt_min_deg=float(point[1]))
                                    )
                        except Exception:
                            flash(_("Warning: Horizon Mask for '%(location_name)s' was invalid and ignored.", location_name=loc.name), "warning")

                    # 3. Assign the new list directly to the relationship.
                    # SQLAlchemy will now compare the old list with the new one.
                    # It will automatically delete any points not in the new list (due to 'delete-orphan')
                    # and add any new points. This avoids the bulk-delete conflict.
                    loc.horizon_points = new_horizon_points
                    # --- END FIX ---

                message = "Locations"

            # --- Update Existing Objects ---
            elif 'submit_objects' in request.form:
                # 1. Fetch all objects for the current user
                objs_to_update = db.query(AstroObject).filter_by(user_id=app_db_user.id).all()

                # 2. Loop through each object and process its form data
                for obj in objs_to_update:
                    # Handle deletion first
                    if request.form.get(f"delete_{obj.object_name}") == "on":
                        db.delete(obj);
                        continue

                    # Update standard fields
                    obj.common_name = request.form.get(f"name_{obj.object_name}")
                    obj.ra_hours = float(request.form.get(f"ra_{obj.object_name}"))
                    obj.dec_deg = float(request.form.get(f"dec_{obj.object_name}"))
                    obj.constellation = request.form.get(f"constellation_{obj.object_name}")
                    obj.project_name = request.form.get(f"project_{obj.object_name}")  # Private notes
                    obj.type = request.form.get(f"type_{obj.object_name}")
                    obj.magnitude = request.form.get(f"magnitude_{obj.object_name}")
                    obj.size = request.form.get(f"size_{obj.object_name}")
                    obj.sb = request.form.get(f"sb_{obj.object_name}")

                    # --- START NEW LOGIC ---
                    # Update the 'ActiveProject' status based on the checkbox being 'on'
                    obj.active_project = request.form.get(f"active_project_{obj.object_name}") == "on"
                    # --- END NEW LOGIC ---

                    if not obj.original_user_id:
                        obj.is_shared = request.form.get(f"is_shared_{obj.object_name}") == "on"
                        obj.shared_notes = request.form.get(f"shared_notes_{obj.object_name}")

                message = "Objects updated."

            if not error:
                db.commit()
                flash(_("%(message)s updated successfully.", message=message or 'Configuration'), "success")
                return redirect(url_for('core.config_form'))
            else:
                db.rollback()
                flash(error, "error")

        # --- GET Request: Populate Template Context from DB ---
        config_for_template = {}
        prefs = db.query(UiPref).filter_by(user_id=app_db_user.id).first()
        if prefs and prefs.json_blob:
            try:
                config_for_template = json.loads(prefs.json_blob)
            except json.JSONDecodeError:
                pass

        # --- START FIX ---
        # Ensure nested dicts are not None, so template .get() calls don't fail
        if config_for_template.get('telemetry') is None:
            config_for_template['telemetry'] = {}
        if config_for_template.get('imaging_criteria') is None:
            config_for_template['imaging_criteria'] = {}
        # --- END FIX ---

        locations_for_template = {}
        db_locations = db.query(Location).options(selectinload(Location.horizon_points)).filter_by(user_id=app_db_user.id).order_by(Location.name).all()
        for loc in db_locations:
            locations_for_template[loc.name] = {
                "lat": loc.lat, "lon": loc.lon, "timezone": loc.timezone,
                "active": loc.active, "comments": loc.comments,
                "horizon_mask": [[hp.az_deg, hp.alt_min_deg] for hp in
                                 sorted(loc.horizon_points, key=lambda p: p.az_deg)]
            }
            if loc.is_default:
                config_for_template['default_location'] = loc.name

        db_objects = db.query(AstroObject).filter_by(user_id=app_db_user.id).order_by(AstroObject.object_name).all()
        config_for_template['objects'] = []
        for o in db_objects:
            # --- START: Rich Text Upgrade for Private Notes ---
            raw_private_notes = o.project_name or ""
            if not raw_private_notes.strip().startswith(
                    ("<p>", "<div>", "<ul>", "<ol>", "<figure>", "<blockquote>", "<h1>", "<h2>", "<h3>", "<h4>", "<h5>",
                     "<h6>")):
                escaped_text = bleach.clean(raw_private_notes, tags=[], strip=True)
                private_notes_html = escaped_text.replace("\n", "<br>")
            else:
                private_notes_html = raw_private_notes
            # --- END: Rich Text Upgrade ---

            # --- START: Rich Text Upgrade for SHARED Notes ---
            raw_shared_notes = o.shared_notes or ""
            if not raw_shared_notes.strip().startswith(("<p>", "<div>", "<ul>", "<ol>")):
                escaped_text = bleach.clean(raw_shared_notes, tags=[], strip=True)
                shared_notes_html = escaped_text.replace("\n", "<br>")
            else:
                shared_notes_html = raw_shared_notes
            # --- END: Rich Text Upgrade ---

            # 1. Get all standard fields from the new method
            obj_data_dict = o.to_dict()

            # 2. Overwrite the note fields with our editor-safe HTML
            obj_data_dict["Project"] = private_notes_html
            obj_data_dict["shared_notes"] = shared_notes_html

            # 3. Append the final dictionary
            config_for_template['objects'].append(obj_data_dict)

        catalog_packs = discover_catalog_packs()
        return render_template('config_form.html', config=config_for_template, locations=locations_for_template, all_timezones=pytz.all_timezones, catalog_packs=catalog_packs)

    except Exception as e:
        db.rollback()
        flash(_("A database error occurred: %(error)s", error=e), "error")
        traceback.print_exc()
        return redirect(url_for('core.index'))


# =============================================================================
# Outlook & Journal API Routes (Batch 3)
# =============================================================================

@core_bp.route('/get_outlook_data')
def get_outlook_data():
    load_full_astro_context()
    # --- Check for guest user first ---
    if hasattr(g, 'is_guest') and g.is_guest:
        return jsonify({"status": "complete", "results": []})

    # --- Determine user ID and username ---
    if SINGLE_USER_MODE:
        user_id = g.db_user.id
        username = g.db_user.username
    elif current_user.is_authenticated:
        user_id = g.db_user.id
        username = g.db_user.username
    else:
        return jsonify({"status": "error", "message": "User not authenticated"}), 401

    # --- Determine Location to Use ---
    requested_location_name = request.args.get('location')
    location_name_to_use = g.selected_location
    if requested_location_name and requested_location_name in g.locations:
        location_name_to_use = requested_location_name
    if not location_name_to_use:
        return jsonify({"status": "error", "message": "No valid location selected or configured."}), 400
    location_name = location_name_to_use

    # --- START OF CHANGES ---
    # 1. Get the new anonymous log ID string
    user_log_key = get_user_log_string(user_id, username)

    # 2. Check for Simulation Mode
    sim_date_str = request.args.get('sim_date')
    # FIX: If no date is simulated (standard view), use empty suffix to match the background cache file.
    date_suffix = f"_{sim_date_str}" if sim_date_str else ""

    # 3. Construct cache filename and status key
    # We append the date suffix so simulated caches don't overwrite the realtime cache
    safe_log_key = user_log_key.replace(" | ", "_").replace(".", "").replace(" ", "_")
    loc_safe = location_name.lower().replace(' ', '_')

    cache_filename = os.path.join(CACHE_DIR, f"outlook_cache_{safe_log_key}_{loc_safe}{date_suffix}.json")
    status_key = f"({user_log_key})_{location_name}{date_suffix}"
    # --- END OF CHANGES ---

    worker_status = cache_worker_status.get(status_key, "idle")
    if worker_status in ["running", "starting"]:
        print(f"[OUTLOOK] Worker for {status_key} is '{worker_status}'. Telling client to wait.")
        return jsonify({"status": worker_status, "results": []})

    if os.path.exists(cache_filename):
        try:
            cache_mtime = os.path.getmtime(cache_filename)
            is_stale = (datetime.now().timestamp() - cache_mtime) > 86400 # 1 day

            if not is_stale:
                with open(cache_filename, 'r') as f:
                    data = json.load(f)

                # Check if cache is from older version (missing has_framing)
                # We look at the first opportunity to see if it has the key
                opportunities = data.get("opportunities", [])
                if opportunities and 'has_framing' not in opportunities[0]:
                    print(f"[OUTLOOK] Cache for {status_key} is missing 'has_framing'. Forcing update.")
                    # Fall through to trigger new worker
                else:
                    return jsonify({"status": "complete", "results": opportunities})
            else:
                print(f"[OUTLOOK] Cache for {status_key} is stale. Will start new worker.")
        except (json.JSONDecodeError, IOError, OSError) as e:
            print(f"❌ ERROR: Could not read/parse outlook cache '{cache_filename}': {e}")

    print(f"[OUTLOOK] Triggering new worker for {status_key} (current status: {worker_status}).")
    try:
        if not hasattr(g, 'user_config') or not g.user_config:
             return jsonify({"status": "error", "message": "User configuration not loaded."}), 500

        sampling_interval = 15 # Default
        if SINGLE_USER_MODE:
            sampling_interval = g.user_config.get('sampling_interval_minutes', 15)
        else:
            sampling_interval = int(os.environ.get('CALCULATION_PRECISION', 15))

        # --- START OF CHANGE (when starting the thread) ---
        # Lazy import to avoid circular dependency
        from nova import update_outlook_cache
        # We pass the user_id (for metadata), the status_key (for logging), and the cache_filename
        thread = threading.Thread(target=update_outlook_cache,
                                  args=(user_id, status_key, cache_filename, location_name, g.user_config.copy(),
                                        sampling_interval, sim_date_str))
        # --- END OF CHANGE ---
        thread.start()
        cache_worker_status[status_key] = "starting"
        return jsonify({"status": "starting", "results": []})

    except Exception as e:
        print(f"❌ ERROR: Failed to start outlook worker thread for {status_key}: {e}")
        traceback.print_exc()
        cache_worker_status[status_key] = "error" # Mark as error if thread start fails
        return jsonify({"status": "error", "message": "Failed to start background worker."}), 500


@core_bp.route('/api/journal/custom-filters', methods=['POST'])
@login_required
def add_custom_filter():
    """Add a new custom filter definition for the current user."""
    import re
    data = request.get_json()
    label = (data.get('label') or '').strip()[:64]
    if not label:
        return jsonify({'error': _('Label required')}), 400

    slug = re.sub(r'[^a-z0-9]+', '_', label.lower()).strip('_')[:40]
    filter_key = f'custom_{slug}'

    db = get_db()
    username = "default" if SINGLE_USER_MODE else current_user.username
    user = db.query(DbUser).filter_by(username=username).one()

    if db.query(UserCustomFilter).filter_by(user_id=user.id, filter_key=filter_key).first():
        return jsonify({'error': _('Filter already exists')}), 409

    db.add(UserCustomFilter(user_id=user.id, filter_key=filter_key, filter_label=label))
    db.commit()
    return jsonify({'key': filter_key, 'label': label}), 201


@core_bp.route('/api/journal/custom-filters/<filter_key>', methods=['DELETE'])
@login_required
def delete_custom_filter(filter_key):
    """Delete a custom filter definition for the current user."""
    db = get_db()
    username = "default" if SINGLE_USER_MODE else current_user.username
    user = db.query(DbUser).filter_by(username=username).one()

    cf = db.query(UserCustomFilter).filter_by(user_id=user.id, filter_key=filter_key).first()
    if not cf:
        return jsonify({'error': _('Filter not found')}), 404
    db.delete(cf)
    db.commit()
    return jsonify({'deleted': filter_key}), 200


@core_bp.route('/trigger_update', methods=['POST'])
def trigger_update():
    try:
        script_path = os.path.join(os.path.dirname(__file__), 'updater.py')
        subprocess.Popen([sys.executable, script_path])
        print("Exiting current app to allow updater to restart it...")
        sys.exit(0)  # Force exit without cleanup
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


# =============================================================================
# Object Search & Details Routes (Batch 4)
# =============================================================================

@core_bp.route('/search_object', methods=['POST'])
@login_required
def search_object():
    # Expect JSON input with the object identifier.
    object_name = request.json.get('object')
    if not object_name:
        return jsonify({"status": "error", "message": _("No object specified.")}), 400

    data = get_ra_dec(object_name)
    if data and data.get("RA (hours)") is not None:
        return jsonify({"status": "success", "data": data})
    else:
        # Return an error message from the lookup.
        return jsonify({"status": "error", "message": data.get("Common Name", _("Object not found."))}), 404


@core_bp.route('/fetch_object_details', methods=['POST'])
@login_required
def fetch_object_details():
    """
    Fetch exactly Type, Magnitude, Size, SB for one object
    using nova_data_fetcher.
    """
    req = request.get_json()
    object_name = req.get("object")
    if not object_name:
        return jsonify({"status": "error", "message": _("No object specified.")}), 400

    try:
        fetched = nova_data_fetcher.get_astronomical_data(object_name)

        record_event('simbad_lookup')
        # --- FIX: Convert NumPy types to native Python types before sending to browser ---
        clean_data = {
            "Type": convert_to_native_python(fetched.get("object_type")),
            "Magnitude": convert_to_native_python(fetched.get("magnitude")),
            "Size": convert_to_native_python(fetched.get("size_arcmin")),
            "SB": convert_to_native_python(fetched.get("surface_brightness"))
        }

        return jsonify({
            "status": "success",
            "data": {
                "Type": clean_data.get("Type") or "",
                "Magnitude": clean_data.get("Magnitude") or "",
                "Size": clean_data.get("Size") or "",
                "SB": clean_data.get("SB") or ""
            }
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@core_bp.route('/confirm_object', methods=['POST'])
@login_required
def confirm_object():
    req = request.get_json()
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        app_db_user = db.query(DbUser).filter_by(username=username).one()

        raw_object_name = req.get('object')
        if not raw_object_name or not raw_object_name.strip():
            raise ValueError("Object ID is required and cannot be empty.")

        # --- NEW: Normalize name ---
        object_name = normalize_object_name(raw_object_name)

        common_name = req.get('name')
        if not common_name or not common_name.strip():
            # If common name is blank, use the raw (pretty) object name as a fallback
            common_name = raw_object_name.strip()

        ra_float = _parse_float_from_request(req.get('ra'), "RA")
        dec_float = _parse_float_from_request(req.get('dec'), "DEC")

        # --- START: Rich Text Logic for Notes ---
        # Get the raw HTML directly from the JS payload
        private_notes_html = req.get('project', '') or ""
        shared_notes_html = req.get('shared_notes', '') or ""
        # --- END: Rich Text Logic ---

        existing = db.query(AstroObject).filter_by(user_id=app_db_user.id, object_name=object_name).one_or_none()

        # Get other fields
        constellation = req.get('constellation')
        obj_type = convert_to_native_python(req.get('type'))
        magnitude = str(convert_to_native_python(req.get('magnitude')) or '')
        size = str(convert_to_native_python(req.get('size')) or '')
        sb = str(convert_to_native_python(req.get('sb')) or '')
        is_shared = req.get('is_shared') == True
        active_project = req.get('is_active') == True

        # Inspiration Fields
        image_url = req.get('image_url')
        image_credit = req.get('image_credit')
        image_source_link = req.get('image_source_link')
        description_text = req.get('description_text')
        description_credit = req.get('description_credit')
        description_source_link = req.get('description_source_link')

        if existing:
            existing.common_name = common_name
            existing.ra_hours = ra_float
            existing.dec_deg = dec_float
            existing.project_name = private_notes_html
            existing.constellation = constellation
            existing.type = obj_type
            existing.magnitude = magnitude
            existing.size = size
            existing.sb = sb
            existing.shared_notes = shared_notes_html
            existing.is_shared = is_shared
            existing.active_project = active_project
            # Update inspiration fields if provided (or clear them if empty string passed)
            if image_url is not None: existing.image_url = image_url
            if image_credit is not None: existing.image_credit = image_credit
            if image_source_link is not None: existing.image_source_link = image_source_link
            if description_text is not None: existing.description_text = description_text
            if description_credit is not None: existing.description_credit = description_credit
            if description_source_link is not None: existing.description_source_link = description_source_link
        else:
            new_obj = AstroObject(
                user_id=app_db_user.id,
                object_name=object_name,
                common_name=common_name,
                ra_hours=ra_float,
                dec_deg=dec_float,
                project_name=private_notes_html,
                constellation=constellation,
                type=obj_type,
                magnitude=magnitude,
                size=size,
                sb=sb,
                shared_notes=shared_notes_html,
                is_shared=is_shared,
                active_project=active_project,
                image_url=image_url,
                image_credit=image_credit,
                image_source_link=image_source_link,
                description_text=description_text,
                description_credit=description_credit,
                description_source_link=description_source_link
            )
            db.add(new_obj)

        db.commit()
        return jsonify({"status": "success"})

    except ValueError as ve:
        db.rollback()
        return jsonify({"status": "error", "message": str(ve)}), 400
    except Exception as e:
        db.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500


@core_bp.route('/stream_fetch_details')
@login_required
def stream_fetch_details():
    """
    Streams progress of fetching object details via Server-Sent Events (SSE).
    """

    @stream_with_context
    def generate():
        username = "default" if SINGLE_USER_MODE else current_user.username
        db = SessionLocal()  # Use a dedicated session for this generator
        try:
            app_db_user = db.query(DbUser).filter_by(username=username).one()
            objects_to_check = db.query(AstroObject).filter_by(user_id=app_db_user.id).all()

            total_count = len(objects_to_check)
            modified_count = 0

            # Send initial open event
            yield f"data: {json.dumps({'progress': 0, 'message': 'Starting analysis...'})}\n\n"

            refetch_triggers = [None, "", "N/A", "Not Found", "Fetch Error"]

            for i, obj in enumerate(objects_to_check):
                # Calculate percentage
                pct = int((i / total_count) * 100)
                yield f"data: {json.dumps({'progress': pct, 'message': f'Checking {obj.object_name}...'})}\n\n"

                needs_update = (
                        obj.type in refetch_triggers or
                        obj.magnitude in refetch_triggers or
                        obj.size in refetch_triggers or
                        obj.sb in refetch_triggers or
                        obj.constellation in refetch_triggers
                )

                if needs_update:
                    item_modified = False
                    try:
                        # 1. Constellation Auto-Calc
                        if obj.constellation in refetch_triggers and obj.ra_hours is not None and obj.dec_deg is not None:
                            coords = SkyCoord(ra=obj.ra_hours * u.hourangle, dec=obj.dec_deg * u.deg)
                            obj.constellation = get_constellation(coords, short_name=True)
                            item_modified = True

                        # 2. External API Fetch
                        yield f"data: {json.dumps({'progress': pct, 'message': f'Fetching data for {obj.object_name}...'})}\n\n"
                        fetched_data = nova_data_fetcher.get_astronomical_data(obj.object_name)

                        if fetched_data.get("object_type"):
                            obj.type = fetched_data["object_type"]
                            item_modified = True
                        if fetched_data.get("magnitude"):
                            obj.magnitude = str(fetched_data["magnitude"])
                            item_modified = True
                        if fetched_data.get("size_arcmin"):
                            obj.size = str(fetched_data["size_arcmin"])
                            item_modified = True
                        if fetched_data.get("surface_brightness"):
                            obj.sb = str(fetched_data["surface_brightness"])
                            item_modified = True

                        if item_modified:
                            modified_count += 1
                            time.sleep(0.5)  # Polite delay

                    except Exception as e:
                        print(f"Failed details fetch for {obj.object_name}: {e}")
                        # Continue stream despite individual object error

            if modified_count > 0:
                yield f"data: {json.dumps({'progress': 99, 'message': 'Saving changes...'})}\n\n"
                db.commit()

            # Send final done signal
            yield f"data: {json.dumps({'progress': 100, 'message': 'Complete!', 'done': True, 'modified': modified_count})}\n\n"

        except Exception as e:
            print(f"Stream Fetch Error: {e}")
            db.rollback()
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
        finally:
            db.close()

    # --- FIX FOR NGINX BUFFERING ---
    response = Response(generate(), mimetype='text/event-stream')
    response.headers["X-Accel-Buffering"] = "no"  # Disable Nginx buffering
    response.headers["Cache-Control"] = "no-cache" # Prevent browser caching
    response.headers["Connection"] = "keep-alive" # Keep connection open
    return response


@core_bp.route('/fetch_all_details', methods=['POST'])
@login_required
def fetch_all_details():
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        app_db_user = db.query(DbUser).filter_by(username=username).one()
        objects_to_check = db.query(AstroObject).filter_by(user_id=app_db_user.id).all()

        modified = False
        refetch_triggers = [None, "", "N/A", "Not Found", "Fetch Error"]

        for obj in objects_to_check:
            needs_update = (
                obj.type in refetch_triggers or
                obj.magnitude in refetch_triggers or
                obj.size in refetch_triggers or
                obj.sb in refetch_triggers or
                obj.constellation in refetch_triggers
            )
            if needs_update:
                try:
                    # Auto-calculate Constellation if missing
                    if obj.constellation in refetch_triggers and obj.ra_hours is not None and obj.dec_deg is not None:
                        coords = SkyCoord(ra=obj.ra_hours*u.hourangle, dec=obj.dec_deg*u.deg)
                        obj.constellation = get_constellation(coords, short_name=True)
                        modified = True

                    # Fetch other details from external API
                    fetched_data = nova_data_fetcher.get_astronomical_data(obj.object_name)
                    if fetched_data.get("object_type"): obj.type = fetched_data["object_type"]
                    if fetched_data.get("magnitude"): obj.magnitude = str(fetched_data["magnitude"])
                    if fetched_data.get("size_arcmin"): obj.size = str(fetched_data["size_arcmin"])
                    if fetched_data.get("surface_brightness"): obj.sb = str(fetched_data["surface_brightness"])
                    modified = True
                    time.sleep(0.5) # Be kind to external APIs
                except Exception as e:
                    print(f"Failed to fetch details for {obj.object_name}: {e}")

        if modified:
            db.commit()
            flash(_("Fetched and saved missing object details."), "success")
        else:
            flash(_("No missing data found or no updates needed."), "info")

    except Exception as e:
        db.rollback()
        flash(_("An error occurred during data fetching: %(error)s", error=e), "error")

    return redirect(url_for('core.config_form'))


# =============================================================================
# Dashboard & Projects Routes (Batch 5)
# =============================================================================

@core_bp.route('/')
def index():
    load_full_astro_context()
    if not (current_user.is_authenticated or SINGLE_USER_MODE or getattr(g, 'is_guest', False)):
        return redirect(url_for('core.login'))

    username = "default" if SINGLE_USER_MODE else current_user.username if current_user.is_authenticated else "guest_user"
    db = get_db()
    user = db.query(DbUser).filter_by(username=username).one_or_none()
    if not user:
        # Handle case where user is authenticated but not yet in app.db
        return render_template('index.html', journal_sessions=[])

    sessions = db.query(JournalSession).filter_by(user_id=user.id).order_by(JournalSession.date_utc.desc()).all()
    all_projects = db.query(Project).filter_by(user_id=user.id).all()
    project_map = {p.id: p.name for p in all_projects}
    objects_from_db = db.query(AstroObject).filter_by(user_id=user.id).all()
    object_names_lookup = {o.object_name: o.common_name for o in objects_from_db}

    # --- THIS IS THE CRITICAL FIX ---
    # Convert the list of objects into a list of JSON-safe dictionaries
    sessions_for_template = []
    for session in sessions:
        # Create a dictionary from the database object's columns
        session_dict = {c.name: getattr(session, c.name) for c in session.__table__.columns}

        # Convert the date object to an ISO string for JavaScript
        if isinstance(session_dict.get('date_utc'), (datetime, date)):
            session_dict['date_utc'] = session_dict['date_utc'].isoformat()

        # Add the common name for convenience in the template
        session_dict['target_common_name'] = object_names_lookup.get(session.object_name, session.object_name)

        if session.project_id:
            session_dict['project_name'] = project_map.get(session.project_id, "Unknown Project")
        else:
            session_dict['project_name'] = "-"  # Or "Standalone"

        sessions_for_template.append(session_dict)
    # --- END OF FIX ---

    local_tz = pytz.timezone(g.tz_name or 'UTC')
    now_local = datetime.now(local_tz)

    # --- START FIX: Determine "Observing Night" Date ---
    # If it's before noon, we're still on the "night of" the previous day.
    if now_local.hour < 12:
        observing_date_for_calcs = now_local.date() - timedelta(days=1)
    else:
        observing_date_for_calcs = now_local.date()
    # --- END FIX ---

    # Get hiding preference (safe default False)
    hide_invisible_pref = g.user_config.get('hide_invisible', True)

    record_event('dashboard_load')
    return render_template('index.html',
                           journal_sessions=sessions_for_template,
                           selected_day=observing_date_for_calcs.day,
                           selected_month=observing_date_for_calcs.month,
                           selected_year=observing_date_for_calcs.year,
                           hide_invisible=hide_invisible_pref)


@core_bp.route('/sun_events')
def sun_events():
    """
    API endpoint to calculate and return sun event times (dusk, dawn, etc.)
    and the current moon phase for a specific location. Uses of the location
    specified in the 'location' query parameter or falls back to the
    user's default location.
    """
    load_full_astro_context()
    # --- Determine location to use ---
    requested_location_name = request.args.get('location')
    lat, lon, tz_name = g.lat, g.lon, g.tz_name # Defaults from flask global 'g'

    # Prioritize the location passed in the query parameter
    if requested_location_name and requested_location_name in g.locations:
        loc_cfg = g.locations[requested_location_name]
        lat = loc_cfg.get("lat")
        lon = loc_cfg.get("lon")
        tz_name = loc_cfg.get("timezone", "UTC")
        # print(f"[API Sun Events] Using requested location: {requested_location_name}") # Optional debug print
    elif g.selected_location and g.selected_location in g.locations:
         # Fallback to default location if request param is missing/invalid but default exists
         loc_cfg = g.locations[g.selected_location]
         lat = loc_cfg.get("lat", g.lat) # Use default g value if key missing in specific config
         lon = loc_cfg.get("lon", g.lon)
         tz_name = loc_cfg.get("timezone", g.tz_name or "UTC") # Use g.tz_name as fallback if timezone missing
         # print(f"[API Sun Events] Using default location: {g.selected_location}") # Optional debug print
    else:
         # print(f"[API Sun Events] Warning: No location specified or default found.") # Optional debug print
         # lat, lon, tz_name remain initial g values (which might be None)
         pass # Proceed, error handled below

    # If after checks, we don't have valid coordinates, return an error immediately
    if lat is None or lon is None:
        # print("[API Sun Events] Error: Invalid coordinates (lat or lon is None).") # Optional debug print
        return jsonify({
            "date": datetime.now().strftime('%Y-%m-%d'),
            "time": datetime.now().strftime('%H:%M'),
            "phase": 0, # Default phase
            "error": "No location set or location has invalid coordinates."
        }), 400 # Bad request status
    # --- END Location Determination ---

    # --- Use of determined (valid) lat, lon, tz_name variables below ---
    try:
        local_tz = pytz.timezone(tz_name)  # Use determined tz_name
    except pytz.UnknownTimeZoneError:
        # Handle invalid timezone string
        # print(f"[API Sun Events] Error: Invalid timezone '{tz_name}'. Falling back to UTC.") # Optional debug print
        tz_name = "UTC"
        local_tz = pytz.utc

        # --- SIMULATION MODE ---
    sim_date_str = request.args.get('sim_date')
    if sim_date_str:
        try:
            sim_date = datetime.strptime(sim_date_str, '%Y-%m-%d').date()
            now_time = datetime.now(local_tz).time()
            now_local = local_tz.localize(datetime.combine(sim_date, now_time))
        except ValueError:
            now_local = datetime.now(local_tz)
    else:
        now_local = datetime.now(local_tz)

    local_date = now_local.strftime('%Y-%m-%d')

    # Calculate sun events using determined variables
    events = calculate_sun_events_cached(local_date, tz_name, lat, lon)

    # Calculate moon phase using determined variables
    try:
        moon = ephem.Moon()
        observer = ephem.Observer()
        observer.lat = str(lat) # Use determined lat (ephem needs string)
        observer.lon = str(lon) # Use determined lon (ephem needs string)
        observer.date = now_local.astimezone(pytz.utc) # Use current time converted to UTC
        moon.compute(observer)
        moon_phase = round(moon.phase, 1)
    except Exception as e:
        # Handle potential errors during ephem calculation
        print(f"[API Sun Events] Error calculating moon phase: {e}") # Log error
        moon_phase = "N/A" # Indicate error in response

    # Add all data to the response JSON
    events["date"] = local_date
    events["time"] = now_local.strftime('%H:%M')
    events["phase"] = moon_phase # Use calculated (or N/A) phase
    # Add error field if moon phase calculation failed
    if moon_phase == "N/A":
        events["error"] = events.get("error","") + " Moon phase calculation failed."

    return jsonify(events)


@core_bp.route('/update_project', methods=['POST'])
@login_required
def update_project():
    data = request.get_json()
    object_name = data.get('object')

    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        user = db.query(DbUser).filter_by(username=username).one()
        obj_to_update = db.query(AstroObject).filter_by(user_id=user.id, object_name=object_name).one_or_none()

        if obj_to_update:

            did_change_active_status = False

            # --- START OF FIX ---
            # 1. Update notes if 'project' key was sent
            if 'project' in data:
                new_project_notes_html = data.get('project')
                obj_to_update.project_name = new_project_notes_html

            # 2. RESTORED: Update Active Status if 'is_active' key was sent
            # This is required for to 'Save Project' button in graph dashboard
            if 'is_active' in data:
                new_active_status = bool(data.get('is_active'))
                if obj_to_update.active_project != new_active_status:
                    obj_to_update.active_project = new_active_status
                    did_change_active_status = True
            # --- END OF FIX ---

            db.commit()

            # Only trigger expensive outlook update if status actually changed
            if did_change_active_status:
                from nova import trigger_outlook_update_for_user  # Lazy import
                trigger_outlook_update_for_user(username)

            return jsonify({"status": "success"})
        else:
            return jsonify({"status": "error", "error": _("Object not found.")}), 404

    except Exception as e:
        db.rollback()
        return jsonify({"status": "error", "error": str(e)}), 500


@core_bp.route('/update_project_active', methods=['POST'])
@login_required
def update_project_active():
    data = request.get_json()
    object_name = data.get('object')
    is_active = data.get('active')
    username = "default" if SINGLE_USER_MODE else current_user.username
    db = get_db()
    try:
        user = db.query(DbUser).filter_by(username=username).one()
        obj_to_update = db.query(AstroObject).filter_by(user_id=user.id, object_name=object_name).one_or_none()

        if obj_to_update:
            obj_to_update.active_project = bool(is_active)
            db.commit()
            from nova import trigger_outlook_update_for_user  # Lazy import
            trigger_outlook_update_for_user(username)
            return jsonify({"status": "success", "active": obj_to_update.active_project})
        else:
            return jsonify({"status": "error", "error": _("Object not found.")}), 404
    except Exception as e:
        db.rollback()
        return jsonify({"status": "error", "error": str(e)}), 500


@core_bp.route('/analytics')
def analytics_dashboard():
    """
    Protected analytics dashboard showing feature usage and login activity.
    Access requires a secret token from .env: ?secret=YOUR_TOKEN
    """
    secret = os.getenv('ANALYTICS_SECRET', '')
    if not secret or request.args.get('secret') != secret:
        abort(403)

    session = SessionLocal()
    try:
        # Last 90 days of event data
        today = date.today()
        since = today - timedelta(days=90)
        days_count = 90

        # Aggregate events: total count and active days per event name
        from sqlalchemy import select, func as sql_func
        stmt = select(
            AnalyticsEvent.event_name,
            sql_func.sum(AnalyticsEvent.count).label('total'),
            sql_func.count(AnalyticsEvent.date).label('active_days')
        ).where(
            AnalyticsEvent.date >= since
        ).group_by(
            AnalyticsEvent.event_name
        ).order_by(
            sql_func.sum(AnalyticsEvent.count).desc()
        )
        events = session.execute(stmt).all()

        # Login data for chart (90 days) - build a map for quick lookup
        login_stmt = select(AnalyticsLogin).where(
            AnalyticsLogin.date >= since
        ).order_by(AnalyticsLogin.date)
        login_rows = session.execute(login_stmt).scalars().all()

        # Recurrence signal: days with at least 1 login in last 30 days
        last_30 = today - timedelta(days=30)
        active_stmt = select(sql_func.count()).select_from(AnalyticsLogin).where(
            AnalyticsLogin.date >= last_30,
            AnalyticsLogin.login_count > 0
        )
        active_days_30 = session.execute(active_stmt).scalar() or 0

        # User registration stats from existing DbUser model
        user_stmt = select(sql_func.count()).select_from(DbUser)
        total_users = session.execute(user_stmt).scalar() or 0
    finally:
        session.close()

    # Build a complete 90-day login series with zeros for missing days
    login_map = {row.date: row.login_count for row in login_rows}
    max_login = max(login_map.values()) if login_map else 1
    login_series = []
    for i in range(days_count):
        day = since + timedelta(days=i)
        count = login_map.get(day, 0)
        height_pct = (count / max_login * 100) if max_login > 0 and count > 0 else 0
        login_series.append({
            'date_str': day.strftime('%Y-%m-%d'),
            'count': count,
            'height_pct': height_pct,
            'has_data': count > 0
        })

    # Pre-compute total logins
    total_logins = sum(login_map.values())

    # Pre-format event data for template
    events_formatted = []
    for event in events:
        events_formatted.append({
            'event_name': event.event_name,
            'total': event.total,
            'total_formatted': "{:,}".format(event.total),
            'active_days': event.active_days
        })

    return render_template('analytics.html',
        events=events_formatted,
        login_series=login_series,
        active_days_30=active_days_30,
        total_users=total_users,
        total_logins=total_logins,
        since_str=since.strftime('%b %d'),
        today_str=today.strftime('%b %d'),
        days_count=days_count
    )
