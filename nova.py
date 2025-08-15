"""
Nova DSO Tracker
------------------------
This application provides endpoints to fetch and plot astronomical data
based on user-specific configuration details (e.g., locations and objects).
It uses Astroquery, Astropy, Ephem, and Matplotlib to calculate object altitudes,
transit times, and generate altitude curves for both celestial objects and the Moon.
It also integrates Flask-Login for user authentication.

March 2025, Anton Gutscher

"""

# =============================================================================
# Imports
# =============================================================================
import os
from datetime import datetime, timedelta, timezone
from decouple import config
from ics import Calendar, Event
import arrow
import requests
import secrets
from dotenv import load_dotenv
import calendar
import json

import pytz
import ephem
import yaml
import shutil
import subprocess
import sys
import time
from modules.config_validation import validate_config
import uuid

import matplotlib
matplotlib.use('Agg')  # Use a non-GUI backend for headless servers
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

from flask import render_template, jsonify, request, send_file, redirect, url_for, flash, g
from flask_login import LoginManager, UserMixin, login_user, login_required, current_user, logout_user
from flask import session, get_flashed_messages, Blueprint
from flask import Flask, send_from_directory

from astroquery.simbad import Simbad
from astropy.coordinates import EarthLocation, AltAz, SkyCoord, get_body
from astropy.time import Time
import astropy.units as u

from modules.astro_calculations import (
    calculate_transit_time,
    get_utc_time_for_local_11pm,
    is_decimal,
    parse_ra_dec,
    hms_to_hours,
    dms_to_degrees,
    ra_dec_to_alt_az,
    calculate_max_observable_altitude,
    calculate_altitude_curve,
    get_common_time_arrays,
    # ephem_to_local,
    # calculate_sun_events,
    calculate_sun_events_cached,
    calculate_observable_duration_vectorized
)

from modules import nova_data_fetcher

# =============================================================================
# Flask and Flask-Login Setup
# =============================================================================

APP_VERSION = "2.8.4"

SINGLE_USER_MODE = config('SINGLE_USER_MODE',  default='True') == 'True'

load_dotenv()
static_cache = {}
moon_separation_cache = {}
config_cache = {}
config_mtime = {}
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config_default.yaml")
ENV_FILE = ".env"
STELLARIUM_ERROR_MESSAGE = os.getenv("STELLARIUM_ERROR_MESSAGE")

# --- Stellarium API URL Configuration ---
# Default URL for running directly on the host
DEFAULT_STELLARIUM_HOST_URL = "http://localhost:8090"
# Special DNS name for Docker Desktop to access the host
DOCKER_DESKTOP_HOST_URL = "http://host.docker.internal:8090"

# Start with the standard default
stellarium_api_url = DEFAULT_STELLARIUM_HOST_URL
print(f"[INIT] Default Stellarium URL: {stellarium_api_url}")

# Check if running inside a Docker container by looking for /.dockerenv
if os.path.exists('/.dockerenv'):
    print("[INIT] Docker environment detected (found /.dockerenv).")
    print(f"[INIT] Attempting to use Docker Desktop host URL for Stellarium: {DOCKER_DESKTOP_HOST_URL}")
    stellarium_api_url = DOCKER_DESKTOP_HOST_URL
    # Note: For Linux Docker (non-Docker Desktop), host.docker.internal might not resolve.
    # In such cases, setting the STELLARIUM_API_URL_BASE environment variable is recommended.
else:
    print("[INIT] Not a Docker environment (/.dockerenv not found).")

# Allow the environment variable to override any automatic detection (highest priority)
STELLARIUM_API_URL_BASE_ENV_VAR = os.getenv("STELLARIUM_API_URL_BASE")
if STELLARIUM_API_URL_BASE_ENV_VAR:
    print(f"[INIT] Environment variable STELLARIUM_API_URL_BASE is set to: '{STELLARIUM_API_URL_BASE_ENV_VAR}'. This will be used.")
    STELLARIUM_API_URL_BASE = STELLARIUM_API_URL_BASE_ENV_VAR
else:
    STELLARIUM_API_URL_BASE = stellarium_api_url
    if os.path.exists('/.dockerenv'):
        print(f"[INIT] STELLARIUM_API_URL_BASE environment variable not set. Using auto-detected Docker host URL: {STELLARIUM_API_URL_BASE}")
    else:
        print(f"[INIT] STELLARIUM_API_URL_BASE environment variable not set. Using default host URL: {STELLARIUM_API_URL_BASE}")

print(f"[INIT] Final Stellarium API URL base for requests: {STELLARIUM_API_URL_BASE}")
# --- End of Stellarium API URL Configuration ---

# Automatically create .env if it doesn't exist
if not os.path.exists(ENV_FILE):
    secret_key = secrets.token_hex(32)

    default_user = "admin"
    default_password = "admin123"

    with open(ENV_FILE, "w") as f:
        f.write(f"SECRET_KEY={secret_key}\n")
        f.write(f"USERS={default_user}\n")  # Add default user
        f.write(f"USER_{default_user.upper()}_ID={default_user}\n")
        f.write(f"USER_{default_user.upper()}_USERNAME={default_user}\n")
        f.write(f"USER_{default_user.upper()}_PASSWORD={default_password}\n")

    print(f"Created .env file with a new SECRET_KEY and default user")

# Load SECRET_KEY and users from the .env file
SECRET_KEY = config('SECRET_KEY', default=secrets.token_hex(32))  # Ensure a fallback key

app = Flask(__name__)
app.secret_key = SECRET_KEY

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = None

# Load user credentials dynamically from .env
usernames = config("USERS", default="").split(",")

users = {}

def python_format_date_eu(value_iso_str):
    """Jinja filter to convert YYYY-MM-DD string to DD.MM.YYYY string."""
    if not value_iso_str or not isinstance(value_iso_str, str):
        return value_iso_str  # Return as is if not a valid string
    try:
        # If it's already DD.MM.YYYY (e.g. from form input passed back on error)
        if '.' in value_iso_str and len(value_iso_str.split('.')[0]) <= 2:
            try:
                # Validate it is indeed DD.MM.YYYY then return it
                datetime.strptime(value_iso_str, '%d.%m.%Y')
                return value_iso_str
            except ValueError:
                # It had dots but wasn't DD.MM.YYYY, so try parsing as YYYY-MM-DD
                pass # Fall through to YYYY-MM-DD parsing

        date_obj = datetime.strptime(value_iso_str, '%Y-%m-%d')
        return date_obj.strftime('%d.%m.%Y')
    except ValueError:
        return value_iso_str  # Return original if any parsing fails

app.jinja_env.filters['date_eu'] = python_format_date_eu

for username in usernames:
    username = username.strip()
    if username:
        user_id = config(f"USER_{username.upper()}_ID", default=username)
        user_username = config(f"USER_{username.upper()}_USERNAME", default=username)
        user_password = config(f"USER_{username.upper()}_PASSWORD", default="changeme")

        users[username] = {
            "id": user_id,
            "username": user_username,
            "password": user_password,
        }

class User(UserMixin):
    def __init__(self, user_id, username):
        self.id = user_id
        self.username = username

def load_journal(username):
    """Loads journal data for the given username from a YAML file."""
    if SINGLE_USER_MODE:
        filename = "journal_default.yaml"
    else:
        filename = f"journal_{username}.yaml"
    filepath = os.path.join(os.path.dirname(__file__), filename)

    if not os.path.exists(filepath):
        print(f"ⓘ Journal file '{filename}' not found. Returning empty journal.")
        return {"sessions": []}  # Return structure with empty sessions list

    try:
        with open(filepath, "r", encoding="utf-8") as file: # Added encoding
            data = yaml.safe_load(file)
            if data is None: # Handles empty YAML file
                print(f"ⓘ Journal file '{filename}' is empty. Returning empty journal.")
                return {"sessions": []}
            # Ensure 'sessions' key exists and is a list
            if "sessions" not in data or not isinstance(data["sessions"], list):
                print(f"⚠️ Journal file '{filename}' is missing 'sessions' list or it's malformed. Initializing.")
                data["sessions"] = []
            return data
    except Exception as e:
        print(f"❌ ERROR: Failed to load journal '{filename}': {e}")
        return {"sessions": []} # Return default structure on error

def save_journal(username, journal_data):
    """Saves journal data for the given username to a YAML file."""
    if SINGLE_USER_MODE:
        filename = "journal_default.yaml"
    else:
        filename = f"journal_{username}.yaml"
    filepath = os.path.join(os.path.dirname(__file__), filename)

    try:
        with open(filepath, "w", encoding="utf-8") as file: # Added encoding
            yaml.dump(journal_data, file, sort_keys=False, allow_unicode=True, indent=2) # Added indent for readability
        print(f"💾 Journal saved to '{filename}' successfully.")
    except Exception as e:
        print(f"❌ ERROR: Failed to save journal '{filename}': {e}")

def generate_session_id():
    """Generates a unique session ID."""
    return uuid.uuid4().hex

@app.route('/journal')
@login_required # Or handle SINGLE_USER_MODE appropriately if guests can see a default journal
def journal_list_view():
    if SINGLE_USER_MODE:
        username = "default"
        # If you want g.is_guest to be available in journal_list.html for SINGLE_USER_MODE
        # ensure it's set correctly or just pass a specific variable.
        # For SINGLE_USER_MODE, the user is effectively always "logged in".
        is_guest_for_template = False
    elif current_user.is_authenticated:
        username = current_user.username
        is_guest_for_template = False
    else:
        # This case should ideally not be hit if @login_required is effective.
        # If you allow guests to see a specific journal, handle 'guest_user' here.
        # For now, let's assume they get redirected to login.
        # If you reach here due to some other logic, provide default.
        flash("Please log in to view the journal.", "info")
        return redirect(url_for('login'))

    journal_data = load_journal(username)
    sessions = journal_data.get('sessions', [])

    # Optionally sort sessions by date descending before passing to template
    try:
        sessions.sort(key=lambda s: s.get('session_date', '1900-01-01'), reverse=True)
    except Exception as e:
        print(f"Warning: Could not sort journal sessions by date: {e}")


    return render_template('journal_list.html',
                           journal_sessions=sessions,
                           is_guest=is_guest_for_template # Pass if your base.html or journal_list.html needs it
                           )


def safe_float(value_str):
    if value_str is None or str(value_str).strip() == "":  # Check for None and empty string
        return None
    try:
        return float(value_str)
    except ValueError:
        print(f"Warning: Could not convert '{value_str}' to float.")
        return None


def safe_int(value_str):
    if value_str is None or str(value_str).strip() == "":  # Check for None and empty string
        return None
    try:
        # First try float conversion to handle inputs like "10.0" for an int field, then convert to int
        return int(float(value_str))
    except ValueError:
        print(f"Warning: Could not convert '{value_str}' to int.")
        return None


@app.route('/journal/add', methods=['GET', 'POST'])
@login_required
def journal_add():
    if SINGLE_USER_MODE:
        username = "default"
    else:
        if not current_user.is_authenticated:  # Should be caught by @login_required
            flash("Please log in to add a journal entry.", "warning")
            return redirect(url_for('login'))
        username = current_user.username

    if request.method == 'POST':
        try:
            journal_data = load_journal(username)
            if not isinstance(journal_data.get('sessions'), list):
                journal_data['sessions'] = []

            # Define the user's timezone-aware date once
            user_tz = pytz.timezone(g.tz_name if hasattr(g, 'tz_name') and g.tz_name else 'UTC')
            today_date_in_user_tz = datetime.now(user_tz).strftime('%Y-%m-%d')

            new_session_data = {
                "session_id": generate_session_id(),
                "session_date": request.form.get("session_date") or today_date_in_user_tz,
                "target_object_id": request.form.get("target_object_id", "").strip(),
                "location_name": request.form.get("location_name", "").strip(),
                "seeing_observed_fwhm": safe_float(request.form.get("seeing_observed_fwhm")),
                "transparency_observed_scale": request.form.get("transparency_observed_scale", "").strip(),
                "sky_sqm_observed": safe_float(request.form.get("sky_sqm_observed")),
                "weather_notes": request.form.get("weather_notes", "").strip(),
                "telescope_setup_notes": request.form.get("telescope_setup_notes", "").strip(),
                "filter_used_session": request.form.get("filter_used_session", "").strip(),
                "guiding_rms_avg_arcsec": safe_float(request.form.get("guiding_rms_avg_arcsec")),
                "guiding_equipment": request.form.get("guiding_equipment", "").strip(),
                "dither_details": request.form.get("dither_details", "").strip(),
                "acquisition_software": request.form.get("acquisition_software", "").strip(),
                "exposure_time_per_sub_sec": safe_int(request.form.get("exposure_time_per_sub_sec")),
                "number_of_subs_light": safe_int(request.form.get("number_of_subs_light")),
                "gain_setting": safe_int(request.form.get("gain_setting")),
                "offset_setting": safe_int(request.form.get("offset_setting")),
                "camera_temp_setpoint_c": safe_float(request.form.get("camera_temp_setpoint_c")),
                "camera_temp_actual_avg_c": safe_float(request.form.get("camera_temp_actual_avg_c")),
                "binning_session": request.form.get("binning_session", "").strip(),
                "darks_strategy": request.form.get("darks_strategy", "").strip(),
                "flats_strategy": request.form.get("flats_strategy", "").strip(),
                "bias_darkflats_strategy": request.form.get("bias_darkflats_strategy", "").strip(),
                "session_rating_subjective": safe_int(request.form.get("session_rating_subjective")),
                "moon_illumination_session": safe_int(request.form.get("moon_illumination_session")),
                "moon_angular_separation_session": safe_float(request.form.get("moon_angular_separation_session")),
                "filter_L_subs": safe_int(request.form.get("filter_L_subs")),
                "filter_L_exposure_sec": safe_int(request.form.get("filter_L_exposure_sec")),
                "filter_R_subs": safe_int(request.form.get("filter_R_subs")),
                "filter_R_exposure_sec": safe_int(request.form.get("filter_R_exposure_sec")),
                "filter_G_subs": safe_int(request.form.get("filter_G_subs")),
                "filter_G_exposure_sec": safe_int(request.form.get("filter_G_exposure_sec")),
                "filter_B_subs": safe_int(request.form.get("filter_B_subs")),
                "filter_B_exposure_sec": safe_int(request.form.get("filter_B_exposure_sec")),
                "filter_Ha_subs": safe_int(request.form.get("filter_Ha_subs")),
                "filter_Ha_exposure_sec": safe_int(request.form.get("filter_Ha_exposure_sec")),
                "filter_OIII_subs": safe_int(request.form.get("filter_OIII_subs")),
                "filter_OIII_exposure_sec": safe_int(request.form.get("filter_OIII_exposure_sec")),
                "filter_SII_subs": safe_int(request.form.get("filter_SII_subs")),
                "filter_SII_exposure_sec": safe_int(request.form.get("filter_SII_exposure_sec")),
                "general_notes_problems_learnings": request.form.get("general_notes_problems_learnings", "").strip()
            }

            final_session_entry = {}
            for k, v in new_session_data.items():
                is_empty_str_for_non_special_field = isinstance(v, str) and v.strip() == "" and k not in [
                    "target_object_id", "location_name"]
                is_none_for_non_special_field = v is None and k not in ["target_object_id", "location_name"]
                if not (is_empty_str_for_non_special_field or is_none_for_non_special_field):
                    final_session_entry[k] = v
            if "session_id" not in final_session_entry:
                final_session_entry["session_id"] = new_session_data["session_id"]
            if "session_date" not in final_session_entry or not final_session_entry["session_date"]:
                final_session_entry["session_date"] = datetime.now().strftime('%Y-%m-%d')

            journal_data['sessions'].append(final_session_entry)
            save_journal(username, journal_data)
            flash("New journal entry added successfully!", "success")

            target_object_id_for_redirect = final_session_entry.get("target_object_id")
            new_session_id_for_redirect = final_session_entry.get("session_id")

            if target_object_id_for_redirect and target_object_id_for_redirect.strip() != "":
                return redirect(url_for('graph_dashboard', object_name=target_object_id_for_redirect,
                                        session_id=new_session_id_for_redirect))
            else:
                return redirect(url_for('index'))  # Or 'journal_list_view' if you prefer for targetless entries

        except Exception as e:
            flash(f"Error adding journal entry: {e}", "error")
            print(f"❌ ERROR in journal_add POST: {e}")
            return redirect(url_for('journal_add'))  # Back to a fresh add form on error

    # --- GET request logic ---
    available_objects = g.user_config.get("objects", []) if hasattr(g, 'user_config') else []
    available_locations = g.locations if hasattr(g, 'locations') else {}
    default_loc = g.selected_location if hasattr(g, 'selected_location') else ""

    preselected_target_id = request.args.get('target', None)
    entry_for_form = {}

    if preselected_target_id:
        entry_for_form["target_object_id"] = preselected_target_id

    if not entry_for_form.get("location_name") and default_loc:
        entry_for_form["location_name"] = default_loc

    # Get the timezone from the g object, with a safe fallback to UTC
    user_tz = pytz.timezone(g.tz_name if hasattr(g, 'tz_name') and g.tz_name else 'UTC')
    # Get the current date specifically for that timezone
    today_date_in_user_tz = datetime.now(user_tz).strftime('%Y-%m-%d')

    if not entry_for_form.get("session_date"):
        entry_for_form["session_date"] = today_date_in_user_tz

    # Determine Cancel URL for "Add" mode
    cancel_url_for_add = url_for('index')  # Default cancel for a general add
    if preselected_target_id:
        # If adding for a specific target (e.g. from graph_view "Add for this object" button),
        # cancel goes back to that target's graph view (without a session_id).
        cancel_url_for_add = url_for('graph_dashboard', object_name=preselected_target_id)

    return render_template('journal_form.html',
                           form_title="Add New Imaging Session",
                           form_action_url=url_for('journal_add'),
                           submit_button_text="Add Session",
                           available_objects=available_objects,
                           available_locations=available_locations,
                           entry=entry_for_form,  # Contains pre-selected target_object_id, location_name, session_date
                           cancel_url=cancel_url_for_add
                           )


@app.route('/journal/edit/<session_id>', methods=['GET', 'POST'])
@login_required
def journal_edit(session_id):
    if SINGLE_USER_MODE:
        username = "default"
    else:
        if not current_user.is_authenticated:
            flash("Please log in to edit journal entries.", "warning")
            return redirect(url_for('login'))
        username = current_user.username

    journal_data = load_journal(username)
    sessions = journal_data.get('sessions', [])

    session_to_edit = None
    session_index = -1

    for index, session_item in enumerate(sessions):  # Renamed loop variable
        if session_item.get('session_id') == session_id:
            session_to_edit = session_item
            session_index = index
            break

    if session_index == -1 or not session_to_edit:  # More robust check
        flash(f"Journal entry with ID {session_id} not found.", "error")
        return redirect(url_for('journal_list_view'))  # Or url_for('index')

    if request.method == 'POST':
        try:
            updated_session_data = {
                "session_id": session_id,  # Keep original ID
                "session_date": request.form.get("session_date") or session_to_edit.get(
                    "session_date") or datetime.now().strftime('%Y-%m-%d'),
                "target_object_id": request.form.get("target_object_id", "").strip(),
                "location_name": request.form.get("location_name", "").strip(),
                "seeing_observed_fwhm": safe_float(request.form.get("seeing_observed_fwhm")),
                "transparency_observed_scale": request.form.get("transparency_observed_scale", "").strip(),
                "sky_sqm_observed": safe_float(request.form.get("sky_sqm_observed")),
                "weather_notes": request.form.get("weather_notes", "").strip(),
                "telescope_setup_notes": request.form.get("telescope_setup_notes", "").strip(),
                "filter_used_session": request.form.get("filter_used_session", "").strip(),
                "guiding_rms_avg_arcsec": safe_float(request.form.get("guiding_rms_avg_arcsec")),
                "guiding_equipment": request.form.get("guiding_equipment", "").strip(),
                "dither_details": request.form.get("dither_details", "").strip(),
                "acquisition_software": request.form.get("acquisition_software", "").strip(),
                "exposure_time_per_sub_sec": safe_int(request.form.get("exposure_time_per_sub_sec")),
                "number_of_subs_light": safe_int(request.form.get("number_of_subs_light")),
                "gain_setting": safe_int(request.form.get("gain_setting")),
                "offset_setting": safe_int(request.form.get("offset_setting")),
                "camera_temp_setpoint_c": safe_float(request.form.get("camera_temp_setpoint_c")),
                "camera_temp_actual_avg_c": safe_float(request.form.get("camera_temp_actual_avg_c")),
                "binning_session": request.form.get("binning_session", "").strip(),
                "darks_strategy": request.form.get("darks_strategy", "").strip(),
                "flats_strategy": request.form.get("flats_strategy", "").strip(),
                "bias_darkflats_strategy": request.form.get("bias_darkflats_strategy", "").strip(),
                "session_rating_subjective": safe_int(request.form.get("session_rating_subjective")),
                "moon_illumination_session": safe_int(request.form.get("moon_illumination_session")),
                "moon_angular_separation_session": safe_float(request.form.get("moon_angular_separation_session")),
                "filter_L_subs": safe_int(request.form.get("filter_L_subs")),
                "filter_L_exposure_sec": safe_int(request.form.get("filter_L_exposure_sec")),
                "filter_R_subs": safe_int(request.form.get("filter_R_subs")),
                "filter_R_exposure_sec": safe_int(request.form.get("filter_R_exposure_sec")),
                "filter_G_subs": safe_int(request.form.get("filter_G_subs")),
                "filter_G_exposure_sec": safe_int(request.form.get("filter_G_exposure_sec")),
                "filter_B_subs": safe_int(request.form.get("filter_B_subs")),
                "filter_B_exposure_sec": safe_int(request.form.get("filter_B_exposure_sec")),
                "filter_Ha_subs": safe_int(request.form.get("filter_Ha_subs")),
                "filter_Ha_exposure_sec": safe_int(request.form.get("filter_Ha_exposure_sec")),
                "filter_OIII_subs": safe_int(request.form.get("filter_OIII_subs")),
                "filter_OIII_exposure_sec": safe_int(request.form.get("filter_OIII_exposure_sec")),
                "filter_SII_subs": safe_int(request.form.get("filter_SII_subs")),
                "filter_SII_exposure_sec": safe_int(request.form.get("filter_SII_exposure_sec")),
                "general_notes_problems_learnings": request.form.get("general_notes_problems_learnings", "").strip()
            }

            final_updated_entry = {}
            for k, v in updated_session_data.items():
                is_empty_str_for_non_special_field = isinstance(v, str) and v.strip() == "" and k not in [
                    "target_object_id", "location_name", "session_id"]
                is_none_for_non_special_field = v is None and k not in ["target_object_id", "location_name",
                                                                        "session_id"]
                if not (is_empty_str_for_non_special_field or is_none_for_non_special_field):
                    final_updated_entry[k] = v
            if "session_id" not in final_updated_entry:
                final_updated_entry["session_id"] = session_id  # Ensure ID is preserved
            if "session_date" not in final_updated_entry or not final_updated_entry["session_date"]:
                final_updated_entry["session_date"] = session_to_edit.get("session_date") or datetime.now().strftime(
                    '%Y-%m-%d')

            sessions[session_index] = final_updated_entry
            journal_data['sessions'] = sessions
            save_journal(username, journal_data)

            flash_message_target = final_updated_entry.get('target_object_id', session_id[:8] + "...")
            flash_message_date = final_updated_entry.get('session_date', 'entry')
            flash(f"Journal entry for '{flash_message_target}' on {flash_message_date} updated successfully!",
                  "success")

            target_object_id_for_redirect = final_updated_entry.get("target_object_id")
            if target_object_id_for_redirect and target_object_id_for_redirect.strip() != "":
                return redirect(
                    url_for('graph_dashboard', object_name=target_object_id_for_redirect, session_id=session_id))
            else:
                return redirect(url_for('index'))

        except Exception as e:
            flash(f"Error updating journal entry: {e}", "error")
            print(f"❌ ERROR in journal_edit POST for session {session_id}: {e}")
            return redirect(url_for('journal_edit', session_id=session_id))

    # --- GET request logic ---
    available_objects = g.user_config.get("objects", []) if hasattr(g, 'user_config') else []
    available_locations = g.locations if hasattr(g, 'locations') else {}

    # Determine Cancel URL for "Edit" mode
    target_object_id_for_cancel = session_to_edit.get("target_object_id")
    cancel_url_for_edit = url_for('index')  # Default fallback

    if target_object_id_for_cancel and target_object_id_for_cancel.strip() != "":
        cancel_url_for_edit = url_for('graph_dashboard',
                                      object_name=target_object_id_for_cancel,
                                      session_id=session_id)  # Link back to this specific session view
    elif session_id:  # If no target, but we are editing a specific session
        cancel_url_for_edit = url_for('journal_list_view')  # Fallback to main journal list

    return render_template('journal_form.html',
                           form_title=f"Edit Imaging Session (ID: {session_to_edit.get('session_id', '')[:8]}...)",
                           form_action_url=url_for('journal_edit', session_id=session_id),
                           submit_button_text="Save Changes",
                           entry=session_to_edit,  # Pre-fill form with existing session data
                           available_objects=available_objects,
                           available_locations=available_locations,
                           cancel_url=cancel_url_for_edit  # Pass the cancel URL
                           )

@app.route('/journal/delete/<session_id>', methods=['POST'])
@login_required  # Or your custom logic for SINGLE_USER_MODE access
def journal_delete(session_id):
    if SINGLE_USER_MODE:
        username = "default"
    else:
        if not current_user.is_authenticated:  # Should be caught by @login_required
            flash("Please log in to delete journal entries.", "warning")
            return redirect(url_for('login'))  # Or your login route name
        username = current_user.username

    journal_data = load_journal(username)
    sessions = journal_data.get('sessions', [])

    session_to_delete = None
    target_object_id_of_deleted_session = None  # Variable to store the target ID

    for session_item in sessions:  # Changed loop variable to avoid conflict if 'session' is used by Flask
        if session_item.get('session_id') == session_id:
            session_to_delete = session_item
            target_object_id_of_deleted_session = session_item.get('target_object_id')
            break

    if session_to_delete:
        try:
            sessions.remove(session_to_delete)  # Remove the session from the list
            journal_data['sessions'] = sessions  # Assign the modified list back to the main data
            save_journal(username, journal_data)  # Save the updated journal data

            flash_message_target = target_object_id_of_deleted_session if target_object_id_of_deleted_session else "N/A"
            # Provide a more specific flash message, perhaps using a few chars of the ID if target is missing
            flash_id_snippet = session_id[:8] + "..." if session_id and len(session_id) > 8 else session_id
            flash(f"Journal entry for '{flash_message_target}' (ID: {flash_id_snippet}) deleted successfully.",
                  "success")

            # --- CORRECTED REDIRECT LOGIC after successful delete ---
            if target_object_id_of_deleted_session and target_object_id_of_deleted_session.strip() != "":
                print(f"Redirecting after delete to graph_dashboard for object: {target_object_id_of_deleted_session}")
                return redirect(url_for('graph_dashboard', object_name=target_object_id_of_deleted_session))
            else:
                # Fallback if target_object_id was somehow missing or empty from the deleted session
                print(
                    f"Redirecting after delete to main journal list (target_object_id was missing for deleted session).")
                return redirect(url_for('journal_list_view'))  # Or url_for('index') if you prefer
            # --- END OF CORRECTED REDIRECT LOGIC ---

        except Exception as e:  # Catch potential errors during remove or save
            flash(f"Error processing deletion for session ID {session_id}: {e}", "error")
            print(f"ERROR during deletion/save for session {session_id}: {e}")
            # If error during save, redirect back to where the user was, if possible, or a safe page.
            # Re-fetching target_object_id here as it might be lost if session_to_delete was modified
            # This is a basic fallback; more sophisticated state restoration might be needed for complex cases.
            if target_object_id_of_deleted_session and target_object_id_of_deleted_session.strip() != "":
                return redirect(url_for('graph_dashboard', object_name=target_object_id_of_deleted_session,
                                        session_id=session_id))  # Back to object page, session might still appear if save failed
            else:
                return redirect(url_for('journal_list_view'))  # General fallback

    else:  # This else is for 'if session_to_delete:' (i.e., session was not found)
        flash(f"Journal entry with ID {session_id} not found for deletion.", "error")
        # If the session was not found at all, redirecting to journal_list_view or index is appropriate.
        return redirect(url_for('journal_list_view'))

@app.route('/journal/add_for_target/<path:object_name>')
@login_required
def journal_add_for_target(object_name):
    # Redirect to the main add form, passing the object_name as a query parameter
    return redirect(url_for('journal_add', target=object_name))

@login_manager.user_loader
def load_user(user_id):
    for user in users.values():
        if user["id"] == user_id:
            return User(user["id"], user["username"])
    return None

# simbad sometimes needs Ids with a / between numbers. this creates a conflict with the app.
def sanitize_object_name(object_name):
    return object_name.replace("/", "-")

@app.context_processor
def inject_user_mode():
    from flask_login import current_user
    return {
        "SINGLE_USER_MODE": SINGLE_USER_MODE,
        "current_user": current_user,
        "is_guest": getattr(g, "is_guest", False)
    }

@app.route('/logout', methods=['POST'])
def logout():
    logout_user()
    session.clear()  # Optional: reset session if needed
    flash("Logged out successfully!", "success")
    return redirect(url_for('login'))

def get_static_cache_key(obj_name, date_str, location):
    return f"{obj_name.lower()}_{date_str}_{location.lower()}"

def get_static_nightly_values(ra, dec, obj_name, local_date, fixed_time_utc_str, location, lat, lon, tz_name, alt_threshold):
    key = get_static_cache_key(obj_name, local_date, location)
    if key in static_cache:
        return static_cache[key]

    # Otherwise calculate and cache
    alt_11pm, az_11pm = ra_dec_to_alt_az(ra, dec, lat, lon, fixed_time_utc_str)
    transit_time = calculate_transit_time(ra, lat, lon, tz_name)
    altitude_threshold = g.user_config.get("altitude_threshold", 20)
    observable_duration, max_altitude = calculate_observable_duration_vectorized(
        ra, dec, lat, lon, local_date, tz_name, altitude_threshold
    )

    static_cache[key] = {
        "Altitude 11PM": alt_11pm,
        "Azimuth 11PM": az_11pm,
        "Transit Time": transit_time,
        "Observable Duration (min)": int(observable_duration.total_seconds() / 60),
        "Max Altitude (°)": round(max_altitude, 1) if max_altitude is not None else "N/A"
    }
    return static_cache[key]

@app.route('/trigger_update', methods=['POST'])
def trigger_update():
    try:
        script_path = os.path.join(os.path.dirname(__file__), 'updater.py')
        subprocess.Popen([sys.executable, script_path])
        print("Exiting current app to allow updater to restart it...")
        os._exit(0)  # Force exit without cleanup
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

def load_user_config(username):
    global config_cache, config_mtime
    if SINGLE_USER_MODE:
        filename = "config_default.yaml"
    else:
        filename = f"config_{username}.yaml"
    filepath = os.path.join(os.path.dirname(__file__), filename)

    try:
        # Check if the file has changed or if it's not in the cache
        if filepath not in config_cache or not os.path.exists(filepath) or (os.path.exists(filepath) and os.path.getmtime(filepath) > config_mtime.get(filepath, 0)):
            if not os.path.exists(filepath):
                print(f"⚠️ Config file '{filename}' not found. Creating from default.")
                try:
                    shutil.copy("config_default.yaml", filename)
                except FileNotFoundError:
                    print("❌ ERROR: Default config file 'config_default.yaml' is missing!")
                    config_cache[filepath] = {}  # Return empty config to prevent crashes
                    config_mtime[filepath] = 0
                    return {}
                except Exception as e:
                    print(f"❌ ERROR: Failed to create user config: {e}")
                    config_cache[filepath] = {}
                    config_mtime[filepath] = 0
                    return {}

            with open(filepath, "r") as file:
                config_cache[filepath] = yaml.safe_load(file) or {}
            if os.path.exists(filepath):
                config_mtime[filepath] = os.path.getmtime(filepath)
            else:
                config_mtime[filepath] = 0  # Or some default value

            print(f"[LOAD CONFIG] Loading (and caching) {filename}")
        else:
            print(f"[LOAD CONFIG] Loading from cache: {filename}")
        return config_cache[filepath]

    except Exception as e:
        print(f"❌ ERROR: Failed to load user config: {e}")
        return {}  # Return empty config to prevent crashes


def save_user_config(username, config_data):
    if SINGLE_USER_MODE:
        filename = "config_default.yaml"
    else:
        filename = f"config_{username}.yaml"
    with open(filename, "w") as file:
        yaml.dump(config_data, file)

def get_imaging_criteria():
    default_criteria = {
        "min_observable_minutes": 60,
        "min_max_altitude": 30,
        "max_moon_illumination": 20,
        "min_angular_distance": 30,
        "search_horizon_months": 6
    }
    user_criteria = g.user_config.get("imaging_criteria", {})
    return {**default_criteria, **user_criteria}

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        get_flashed_messages(with_categories=True)  # clear old messages

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user_record = users.get(username)
        if user_record and user_record['password'] == password:
            user = User(user_record['id'], user_record['username'])
            login_user(user)
            flash("Logged in successfully!", "success")
            return redirect(url_for('index'))
        else:
            flash("Invalid username or password.", "error")

    return render_template('login.html')


@app.route('/proxy_focus', methods=['POST'])
# @login_required # You had this commented out in your original code, add it if needed
def proxy_focus():
    payload = request.form
    try:
        # This line ensures the dynamically determined STELLARIUM_API_URL_BASE is used:
        stellarium_focus_url = f"{STELLARIUM_API_URL_BASE}/api/main/focus"

        print(f"[PROXY FOCUS] Attempting to connect to Stellarium at: {stellarium_focus_url}")  # For debugging

        # Make the request to Stellarium
        r = requests.post(stellarium_focus_url, data=payload, timeout=10)  # Added timeout
        r.raise_for_status()  # Raise an exception for bad status codes (4xx or 5xx)

        print(f"[PROXY FOCUS] Stellarium response: {r.status_code}")  # For debugging
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

@app.before_request
def load_config_for_request():
    if SINGLE_USER_MODE:
        g.user_config = load_user_config("default")
        g.is_guest = False
    elif current_user.is_authenticated:
        g.user_config = load_user_config(current_user.username)
        g.is_guest = False
    else:
        g.user_config = load_user_config("guest_user")
        g.is_guest = True

    g.locations = g.user_config.get("locations", {})
    g.selected_location = g.user_config.get("default_location", "")
    g.altitude_threshold = g.user_config.get("altitude_threshold", 20)
    loc_cfg = g.locations.get(g.selected_location, {})
    g.lat = loc_cfg.get("lat")
    g.lon = loc_cfg.get("lon")
    g.tz_name = loc_cfg.get("timezone", "UTC")

    # restore these three globals so loops and lookups work:
    g.objects_list = g.user_config.get("objects", [])
    g.alternative_names = {
        obj.get("Object").lower(): obj.get("Name")
        for obj in g.objects_list
    }
    g.projects = {
        obj.get("Object").lower(): obj.get("Project")
        for obj in g.objects_list
    }
    g.objects = [ obj.get("Object") for obj in g.objects_list ]


@app.route('/fetch_all_details', methods=['POST'])
@login_required
def fetch_all_details():
    """Fetches missing details for all objects in the user's config."""
    if SINGLE_USER_MODE:
        username = "default"
    else:
        username = current_user.username

    print(f"[FETCH ALL] Triggered for user: {username}")
    try:
        config_data = load_user_config(username)  # Load current config

        # check_and_fill_object_data modifies config_data in place
        # and returns True if changes were made and need saving
        config_modified = check_and_fill_object_data(config_data)

        if config_modified:
            # Save the potentially modified config
            save_user_config(username, config_data)
            flash("Fetched and saved missing object details.", "success")
            print("[FETCH ALL] Data fetched and config saved.")
        else:
            flash("No missing data found or no updates needed.", "info")
            print("[FETCH ALL] No missing data needed fetching.")

    except Exception as e:
        print(f"[FETCH ALL ERROR] Failed during fetch all process: {e}")
        flash(f"An error occurred during data fetching: {e}", "error")

    # Redirect back to the config form to show updated data/messages
    return redirect(url_for('config_form'))

@app.route('/set_location', methods=['POST'])
def set_location_api():
    data = request.get_json()
    location_name = data.get("location")
    if location_name not in g.locations:
        return jsonify({"status": "error", "message": "Invalid location"}), 404

    # Update in-memory config and selection
    g.user_config['default_location'] = location_name
    g.selected_location = location_name

    # Save to appropriate config file
    username = current_user.username if current_user.is_authenticated else 'guest_user'
    save_user_config(username, g.user_config)

    return jsonify({"status": "success", "message": f"Location set to {location_name}"})


@app.route('/favicon.ico')
def favicon():
    return send_from_directory('static', 'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.context_processor
def inject_version():
    return dict(version=APP_VERSION)

@app.route('/download_config')
def download_config():
    if SINGLE_USER_MODE:
        filename = "config_default.yaml"
    else:
        filename = f"config_{current_user.username}.yaml"

    filepath = os.path.join(os.getcwd(), filename)
    if os.path.exists(filepath):
        return send_file(filepath, as_attachment=True)
    else:
        return "Configuration file not found.", 404

@app.route('/download_journal')
@login_required # Or your custom logic for SINGLE_USER_MODE
def download_journal():
    if SINGLE_USER_MODE:
        filename = "journal_default.yaml"
        # Ensure the user is effectively logged in for single user mode if needed by send_file context
    else:
        if not current_user.is_authenticated: # Should be caught by @login_required
            flash("Please log in to download your journal.", "warning")
            return redirect(url_for('login'))
        username = current_user.username
        filename = f"journal_{username}.yaml"

    # Construct the full path to the file
    # Assuming your journal YAML files are in the same directory as app.py (instance path or app root)
    # If they are in a subdirectory e.g. 'user_data/', adjust the path.
    # For consistency with how load_journal/save_journal build paths:
    filepath = os.path.join(os.path.dirname(__file__), filename)


    if os.path.exists(filepath):
        try:
            return send_file(filepath, as_attachment=True, download_name=filename)
        except Exception as e:
            print(f"Error sending journal file: {e}")
            flash("Error downloading journal file.", "error")
            return redirect(url_for('config_form')) # Or some other appropriate page
    else:
        flash(f"Journal file '{filename}' not found.", "error")
        # If no journal exists, you could offer to download an empty template,
        # but for now, just an error is fine.
        return redirect(url_for('config_form')) # Redirect back to config form


def validate_journal_data(journal_data):
    """
    Basic validation for imported journal data.
    Returns True if valid, False otherwise.
    Can be expanded for more detailed schema validation later.
    """
    if not isinstance(journal_data, dict):
        return False, "Uploaded journal is not a valid dictionary structure."
    if "sessions" not in journal_data:
        return False, "Uploaded journal is missing the top-level 'sessions' key."
    if not isinstance(journal_data["sessions"], list):
        return False, "The 'sessions' key in the uploaded journal must be a list."

    # Optional: Check if each session has a session_id (basic check)
    for i, session in enumerate(journal_data["sessions"]):
        if not isinstance(session, dict):
            return False, f"Session entry at index {i} is not a valid dictionary."
        if "session_id" not in session or not session["session_id"]:
            return False, f"Session entry at index {i} is missing a 'session_id'."
        # Add more checks per session if desired (e.g., session_date format)
    return True, "Journal data seems structurally valid."


@app.route('/import_journal', methods=['POST'])
@login_required  # Or your custom logic
def import_journal():
    if 'file' not in request.files:
        flash("No file selected for journal import.", "error")
        return redirect(url_for('config_form'))

    file = request.files['file']
    if file.filename == '':
        flash("No file selected for journal import.", "error")
        return redirect(url_for('config_form'))

    if file and file.filename.endswith('.yaml'):
        try:
            contents = file.read().decode('utf-8')
            new_journal_data = yaml.safe_load(contents)

            if new_journal_data is None:  # Handle completely empty YAML file
                new_journal_data = {"sessions": []}

            # Basic validation for the journal structure
            is_valid, message = validate_journal_data(new_journal_data)
            if not is_valid:
                flash(f"Invalid journal file structure: {message}", "error")
                return redirect(url_for('config_form'))

            # Determine the correct journal filename for this user
            if SINGLE_USER_MODE:
                username = "default"
                journal_filename = "journal_default.yaml"
            else:
                if not current_user.is_authenticated:
                    flash("Please log in to import a journal.", "warning")
                    return redirect(url_for('login'))
                username = current_user.username
                journal_filename = f"journal_{username}.yaml"

            journal_filepath = os.path.join(os.path.dirname(__file__), journal_filename)

            # Backup current journal file if it exists
            if os.path.exists(journal_filepath):
                backup_dir = os.path.join(os.path.dirname(journal_filepath), "backups")
                os.makedirs(backup_dir, exist_ok=True)
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                backup_filename = f"{journal_filename}_backup_{timestamp}.yaml"
                backup_path = os.path.join(backup_dir, backup_filename)
                try:
                    shutil.copy(journal_filepath, backup_path)
                    print(f"[IMPORT JOURNAL] Backed up current journal to {backup_path}")
                except Exception as backup_e:
                    print(f"Warning: Could not back up existing journal: {backup_e}")

            # Save new journal data (overwrite existing)
            save_journal(username, new_journal_data)  # Use your existing save_journal function

            flash("Journal imported successfully! Your old journal (if any) has been backed up.", "success")
            return redirect(url_for('config_form'))

        except yaml.YAMLError as ye:
            print(f"[IMPORT JOURNAL ERROR] Invalid YAML format: {ye}")
            flash(f"Import failed: Invalid YAML format in the journal file. {ye}", "error")
            return redirect(url_for('config_form'))
        except Exception as e:
            print(f"[IMPORT JOURNAL ERROR] {e}")
            flash(f"Import failed: An unexpected error occurred. {str(e)}", "error")
            return redirect(url_for('config_form'))
    else:
        flash("Invalid file type. Please upload a .yaml journal file.", "error")
        return redirect(url_for('config_form'))


@app.route('/import_config', methods=['POST'])
def import_config():
    try:
        if 'file' not in request.files:
            # Correctly use flash and redirect for user feedback
            flash("No file selected for import.", "error")
            return redirect(url_for('config_form'))

        file = request.files['file']
        if file.filename == '':
            flash("No file selected for import.", "error")
            return redirect(url_for('config_form'))

        contents = file.read().decode('utf-8')
        new_config = yaml.safe_load(contents)

        valid, errors = validate_config(new_config)
        if not valid:
            error_message = f"Configuration validation failed: {json.dumps(errors, indent=2)}"
            flash(error_message, "error")
            return redirect(url_for('config_form'))

        # ====================================================================
        # FIXED LOGIC: Explicitly check SINGLE_USER_MODE
        # ====================================================================
        if SINGLE_USER_MODE:
            username_for_backup = "default"
            config_filename = "config_default.yaml"
        else:
            # Ensure there is an authenticated user in multi-user mode
            if not current_user.is_authenticated:
                flash("You must be logged in to import a configuration.", "error")
                return redirect(url_for('login'))
            username_for_backup = current_user.username
            config_filename = f"config_{username_for_backup}.yaml"

        config_path = os.path.join(os.path.dirname(__file__), config_filename)
        # ====================================================================

        # Backup current config if it exists
        if os.path.exists(config_path):
            backup_dir = os.path.join(os.path.dirname(config_path), "backups")
            os.makedirs(backup_dir, exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            # Use the determined username for the backup file name
            backup_path = os.path.join(backup_dir, f"{username_for_backup}_backup_{timestamp}.yaml")
            shutil.copy(config_path, backup_path)
            print(f"[IMPORT] Backed up current config to {backup_path}")

        # Save new config if valid
        with open(config_path, 'w') as f:
            yaml.dump(new_config, f, default_flow_style=False)

        print(f"[IMPORT] Overwrote {config_path} successfully with new config.")
        flash("Config imported successfully! Your old config (if any) has been backed up.", "success")
        return redirect(url_for('config_form'))

    except yaml.YAMLError as ye:
        print(f"[IMPORT ERROR] Invalid YAML: {ye}")
        flash(f"Import failed: The uploaded file was not valid YAML. ({ye})", "error")
        return redirect(url_for('config_form'))
    except Exception as e:
        print(f"[IMPORT ERROR] {e}")
        flash(f"Import failed: An unexpected error occurred. {str(e)}", "error")
        return redirect(url_for('config_form'))

# =============================================================================
# Astronomical Calculations
# =============================================================================


def get_ra_dec(object_name):
    obj_key = object_name.lower()
    objects_config = g.user_config.get("objects", [])
    obj_entry = next((item for item in objects_config if item["Object"].lower() == obj_key), None)

    default_type = "N/A"
    default_magnitude = "N/A"
    default_size = "N/A"
    default_sb = "N/A"
    default_project = "none"

    if obj_entry:
        ra_str = obj_entry.get("RA")
        dec_str = obj_entry.get("DEC")
        type_val = obj_entry.get("Type", default_type)
        magnitude_val = obj_entry.get("Magnitude", default_magnitude)
        size_val = obj_entry.get("Size", default_size)
        sb_val = obj_entry.get("SB", default_sb)
        project_val = obj_entry.get("Project", default_project)
        common_name_val = obj_entry.get("Name", object_name)

        if ra_str is not None and dec_str is not None:
            try:
                ra_hours_float = float(ra_str)
                dec_degrees_float = float(dec_str)
                return {
                    "Object": object_name,
                    "Common Name": common_name_val,
                    "RA (hours)": ra_hours_float,
                    "DEC (degrees)": dec_degrees_float,
                    "Project": project_val,
                    "Type": type_val if type_val else default_type,
                    "Magnitude": magnitude_val if magnitude_val else default_magnitude,
                    "Size": size_val if size_val else default_size,
                    "SB": sb_val if sb_val else default_sb,
                }
            except ValueError as ve:
                print(f"[ERROR] Failed to parse RA/DEC for {object_name} from config: {ve}")
                return {
                    "Object": object_name,
                    "Common Name": f"Error: Invalid RA/DEC in config",
                    "RA (hours)": None, "DEC (degrees)": None,
                    "Project": project_val, "Type": type_val if type_val else default_type,
                    "Magnitude": magnitude_val if magnitude_val else default_magnitude,
                    "Size": size_val if size_val else default_size,
                    "SB": sb_val if sb_val else default_sb,
                }
        else:  # RA/DEC are missing in config for an existing obj_entry
            print(f"[SIMBAD] RA/DEC missing for {object_name} in config. Querying SIMBAD...")
            # Fall through to SIMBAD lookup logic below
            pass  # Explicitly falling through

    # This block handles:
    # 1. Object not in config at all.
    # 2. Object in config but RA/DEC were missing (due to the 'pass' above).
    if not obj_entry or (obj_entry and (obj_entry.get("RA") is None or obj_entry.get("DEC") is None)):
        if not obj_entry:  # Case: Object not found in config
            print(f"[INFO] Object {object_name} not found in config. Attempting SIMBAD lookup.")
        # These values would be from config if obj_entry existed
        common_name_to_use = obj_entry.get("Name", object_name) if obj_entry else object_name
        project_to_use = obj_entry.get("Project", default_project) if obj_entry else default_project
        type_to_use = obj_entry.get("Type", default_type) if obj_entry else default_type
        mag_to_use = obj_entry.get("Magnitude", default_magnitude) if obj_entry else default_magnitude
        size_to_use = obj_entry.get("Size", default_size) if obj_entry else default_size
        sb_to_use = obj_entry.get("SB", default_sb) if obj_entry else default_sb

        try:
            custom_simbad = Simbad()
            custom_simbad.ROW_LIMIT = 1
            custom_simbad.TIMEOUT = 60
            # Explicitly request fields. 'ra' and 'dec' are for J2000 sexagesimal.
            # 'main_id' for a common identifier, 'otype' for object type.
            custom_simbad.add_votable_fields('main_id', 'ra', 'dec', 'otype')

            result = custom_simbad.query_object(object_name)

            if result is None or len(result) == 0:
                raise ValueError(f"No results for object '{object_name}' in SIMBAD.")

            # Crucial Debugging Line:
            print(f"[SIMBAD DEBUG] Columns for {object_name}: {result.colnames}")

            ra_col, dec_col = None, None
            if 'RA' in result.colnames:
                ra_col = 'RA'
            elif 'ra' in result.colnames:
                ra_col = 'ra'  # Check for lowercase
            else:
                raise ValueError(
                    f"SIMBAD result for '{object_name}' is missing RA column. Available: {result.colnames}")

            if 'DEC' in result.colnames:
                dec_col = 'DEC'
            elif 'dec' in result.colnames:
                dec_col = 'dec'  # Check for lowercase
            else:
                raise ValueError(
                    f"SIMBAD result for '{object_name}' is missing DEC column. Available: {result.colnames}")

            ra_value_simbad = str(result[ra_col][0])
            dec_value_simbad = str(result[dec_col][0])

            ra_hours_simbad = hms_to_hours(ra_value_simbad)
            dec_degrees_simbad = dms_to_degrees(dec_value_simbad)

            simbad_main_id = str(result['MAIN_ID'][0]) if 'MAIN_ID' in result.colnames and result['MAIN_ID'][
                0] else common_name_to_use
            simbad_otype = str(result['OTYPE'][0]) if 'OTYPE' in result.colnames and result['OTYPE'][0] else type_to_use

            # If the object was in config but RA/DEC were missing, update the in-memory entry
            if obj_entry:
                obj_entry["RA"] = ra_hours_simbad
                obj_entry["DEC"] = dec_degrees_simbad
                if obj_entry.get("Name") in [None, "",
                                             object_name] and simbad_main_id != object_name:  # Update common name if generic
                    obj_entry["Name"] = simbad_main_id
                if obj_entry.get("Type") in [None, "", "N/A"] and simbad_otype != "N/A":  # Update type if generic
                    obj_entry["Type"] = simbad_otype

            return {
                "Object": object_name,
                "Common Name": simbad_main_id if not obj_entry or obj_entry.get("Name") in [None,
                                                                                            ""] else obj_entry.get(
                    "Name"),
                "RA (hours)": ra_hours_simbad,
                "DEC (degrees)": dec_degrees_simbad,
                "Project": project_to_use,
                "Type": simbad_otype if not obj_entry or obj_entry.get("Type") in [None, "", "N/A"] else obj_entry.get(
                    "Type"),
                "Magnitude": mag_to_use,  # These are not fetched by this basic SIMBAD query
                "Size": size_to_use,
                "SB": sb_to_use,
            }

        except Exception as ex:
            # Provide more detailed error including exception type
            error_message = f"Error: SIMBAD lookup failed ({type(ex).__name__}): {str(ex)}"
            print(f"[ERROR] {error_message} for {object_name}")
            return {
                "Object": object_name,
                "Common Name": error_message,
                "RA (hours)": None, "DEC (degrees)": None,
                "Project": project_to_use, "Type": type_to_use,  # Use defaults or config values if obj_entry existed
                "Magnitude": mag_to_use, "Size": size_to_use, "SB": sb_to_use,
            }

    print(f"[WARN] get_ra_dec: Unhandled case for object {object_name}")
    return {
        "Object": object_name, "Common Name": "Error: Could not determine RA/DEC",
        "RA (hours)": None, "DEC (degrees)": None,
        "Project": default_project, "Type": default_type,
        "Magnitude": default_magnitude, "Size": default_size, "SB": default_sb,
    }

def plot_altitude_curve(object_name, alt_name, ra, dec, lat, lon, local_date, tz_name, selected_location):
    times_local, times_utc = get_common_time_arrays(tz_name, local_date)
    times_local_naive = [t.replace(tzinfo=None) for t in times_local]

    now_local = datetime.now(pytz.timezone(g.tz_name))
    local_tz = pytz.timezone(tz_name)

    # Convert tz-aware local times to naive for plotting.
    times_local_naive = [t.replace(tzinfo=None) for t in times_local]

    # Calculate altitude and azimuth for the object.
    location = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=0 * u.m)
    sky_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
    altaz_frame = AltAz(obstime=times_utc, location=location)
    altaz = sky_coord.transform_to(altaz_frame)
    altitudes = altaz.alt.deg
    azimuths = altaz.az.deg

    # Calculate Moon altitude and azimuth.
    moon_altitudes, moon_azimuths = [], []
    for t_utc in times_utc:
        frame = AltAz(obstime=t_utc, location=location)
        moon_coord = get_body('moon', t_utc, location=location)
        moon_altaz = moon_coord.transform_to(frame)
        moon_altitudes.append(moon_altaz.alt.deg)
        moon_azimuths.append(moon_altaz.az.deg)

    # Get sun events.
    sun_events_curr = calculate_sun_events_cached(local_date,g.tz_name, g.lat, g.lon)
    previous_date = (datetime.strptime(local_date, '%Y-%m-%d') - timedelta(days=1)).strftime('%Y-%m-%d')
    sun_events_prev = calculate_sun_events_cached(previous_date,g.tz_name, g.lat, g.lon)

    # Prepare sun event datetimes.
    event_datetimes = {}
    base_prev = local_tz.localize(datetime.strptime(previous_date, '%Y-%m-%d'))
    for event, t_str in sun_events_prev.items():
        try:
            hour, minute = map(int, t_str.split(':'))
        except Exception:
            continue
        event_dt = base_prev.replace(hour=hour, minute=minute)
        event_datetimes[f'prev_{event}'] = event_dt
    base_curr = local_tz.localize(datetime.strptime(local_date, '%Y-%m-%d'))
    for event, t_str in sun_events_curr.items():
        try:
            hour, minute = map(int, t_str.split(':'))
        except Exception:
            continue
        event_dt = base_curr.replace(hour=hour, minute=minute)
        event_datetimes[f'curr_{event}'] = event_dt

    event_datetimes_naive = {k: v.replace(tzinfo=None) for k, v in event_datetimes.items()}

    # Create figure and primary axis.
    fig, ax = plt.subplots(figsize=(11, 6))
    ax.plot(times_local_naive, altitudes, '-', linewidth=3, color='tab:blue', label=f'{object_name} Altitude')

    #create light gray background
    ax.set_facecolor("lightgray")

    # Plot Moon altitude as dashed yellow.
    ax.plot(times_local_naive, moon_altitudes, '-', color='gold', linewidth=2.5, label='Moon Altitude')

    # Plot Horizon
    ax.axhline(y=0, color='black', linewidth=2, linestyle='-', label='Horizon')

    ax.set_xlabel(f'Time (Local - {selected_location})')
    ax.set_ylabel('Altitude (°)', color='k')
    ax.tick_params(axis='y', labelcolor='k')
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
    ax.xaxis.set_major_locator(mdates.HourLocator(interval=2))
    plt.xticks(rotation=45)

    # Fix altitude axis to range from -90 to +90 degrees.
    ax.set_ylim(-90, 90)

    # Add a two-line title.
    ax.set_title(f"Altitude and Azimuth for {object_name} ({alt_name}) on {local_date}", loc='left')

    # Create secondary axis for azimuth.
    ax2 = ax.twinx()
    ax2.plot(times_local_naive, azimuths, '--', linewidth=1.5, color='tab:cyan', label=f'{object_name} Azimuth')
    ax2.set_ylabel('Azimuth (°)', color='k')
    ax2.tick_params(axis='y', labelcolor='k')
    ax2.set_ylim(0, 360)
    ax2.spines['right'].set_color('k')
    ax2.spines['right'].set_linewidth(1.5)

    # Add Moon azimuth.
    ax2.plot(times_local_naive, moon_azimuths, '--', linewidth=1.5, color='gold', label='Moon Azimuth')

    # Set x-axis limits.
    plot_start = times_local_naive[0]
    plot_end = plot_start + timedelta(hours=24)
    ax.set_xlim(plot_start, plot_end)

    # Define key times correctly
    midnight = datetime.combine(datetime.strptime(local_date, '%Y-%m-%d'), datetime.min.time())
    noon = midnight + timedelta(hours=12)
    previous_midnight = midnight - timedelta(days=1)

    # Define plot_start and plot_end assuming times_local_naive covers the selected day.
    # Define 24-hour plot window
    plot_start = times_local_naive[0]
    plot_end = plot_start + timedelta(hours=24)

# --- Corrected Background Shading and Event Line Logic ---
    ax.set_facecolor("lightgray") # Set default background

    # Define plot start and end clearly (assuming times_local_naive covers the desired 24h)
    plot_start = times_local_naive[0]
    plot_end = times_local_naive[-1] # Use the actual end time from your array

    # Calculate sun events for current and next day
    sun_events_curr = calculate_sun_events_cached(local_date, g.tz_name, g.lat, g.lon)
    next_date_obj = datetime.strptime(local_date, '%Y-%m-%d') + timedelta(days=1)
    next_date_str = next_date_obj.strftime('%Y-%m-%d')
    sun_events_next = calculate_sun_events_cached(next_date_str, g.tz_name, g.lat, g.lon)

    # --- Prepare Event Datetimes with Correct Date Assignment ---
    event_datetimes = {}
    curr_date_obj = datetime.strptime(local_date, '%Y-%m-%d')

    # Helper function to parse and localize time strings safely
    def get_event_datetime(base_date_obj, event_time_str, tz):
        if not event_time_str or ':' not in event_time_str:
            return None # Handle missing or invalid time
        try:
            event_time = datetime.strptime(event_time_str, "%H:%M").time()
            dt_naive = datetime.combine(base_date_obj, event_time)
            return tz.localize(dt_naive)
        except ValueError:
            return None # Handle parsing errors

    # Get key times (handle potential missing events)
    astro_dusk_curr_str = sun_events_curr.get("astronomical_dusk")
    astro_dawn_next_str = sun_events_next.get("astronomical_dawn")
    sunrise_curr_str = sun_events_curr.get("sunrise") # Needed for dusk check
    sunset_curr_str = sun_events_curr.get("sunset")
    sunrise_next_str = sun_events_next.get("sunrise") # Use next day's sunrise for line

    # Determine Astronomical Dusk Datetime (Correcting for post-midnight)
    astro_dusk_naive = None
    if astro_dusk_curr_str and ':' in astro_dusk_curr_str:
        try:
            astro_dusk_time = datetime.strptime(astro_dusk_curr_str, "%H:%M").time()
            # Use sunrise as a reference: if dusk is before sunrise, it's on the next calendar day
            sunrise_curr_time = datetime.strptime(sunrise_curr_str, "%H:%M").time() if sunrise_curr_str and ':' in sunrise_curr_str else datetime.time(6, 0) # Default if sunrise missing

            dusk_date_base = next_date_obj if astro_dusk_time < sunrise_curr_time else curr_date_obj

            astro_dusk_dt_naive = datetime.combine(dusk_date_base, astro_dusk_time)
            astro_dusk_localized = local_tz.localize(astro_dusk_dt_naive)
            astro_dusk_naive = astro_dusk_localized.replace(tzinfo=None)
            event_datetimes["Astronomical dusk"] = astro_dusk_naive
        except ValueError:
            print(f"Warning: Could not parse astronomical dusk time: {astro_dusk_curr_str}")
            astro_dusk_naive = None # Ensure it's None if parsing fails

    # Determine Astronomical Dawn Datetime (Next Day)
    astro_dawn_next_naive = None
    astro_dawn_next_localized = get_event_datetime(next_date_obj, astro_dawn_next_str, local_tz)
    if astro_dawn_next_localized:
        astro_dawn_next_naive = astro_dawn_next_localized.replace(tzinfo=None)
        event_datetimes["Astronomical dawn"] = astro_dawn_next_naive

    # --- Shade Night Time ---
    # Shade only if both dusk and dawn times are valid and dusk < dawn
    if astro_dusk_naive and astro_dawn_next_naive and astro_dusk_naive < astro_dawn_next_naive:
         # Clip shading to plot boundaries
         shade_start = max(plot_start, astro_dusk_naive)
         shade_end = min(plot_end, astro_dawn_next_naive)
         # Only shade if the clipped interval is valid
         if shade_start < shade_end:
              ax.axvspan(shade_start, shade_end, facecolor="white", alpha=1.0, zorder=0) # zorder=0 to draw below plot lines


    # --- Add other event times for vertical lines ---
    sunset_curr_localized = get_event_datetime(curr_date_obj, sunset_curr_str, local_tz)
    if sunset_curr_localized:
        event_datetimes["Sunset"] = sunset_curr_localized.replace(tzinfo=None)

    sunrise_next_localized = get_event_datetime(next_date_obj, sunrise_next_str, local_tz)
    if sunrise_next_localized:
         event_datetimes["Sunrise"] = sunrise_next_localized.replace(tzinfo=None)

    # --- Draw Vertical Lines ---
    for event, dt_naive in event_datetimes.items():
        if dt_naive and plot_start <= dt_naive <= plot_end: # Check dt_naive exists
            ax.axvline(x=dt_naive, color='black', linestyle='-', linewidth=1, alpha=0.7, zorder=1) # zorder=1 to draw above shading
            try:
                # Position text slightly after the line, ensuring it stays within bounds
                text_time = dt_naive + timedelta(minutes=5)
                if text_time > plot_end: # Prevent text going off the right edge
                    text_time = dt_naive - timedelta(minutes=5)
                    ha = 'right'
                else:
                    ha = 'left'

                label_x = mdates.date2num(text_time)
                ymin, ymax = ax.get_ylim()
                label_y = ymin + 0.05 * (ymax - ymin) # Position near bottom
                ax.text(label_x, label_y, event, rotation=90,
                        verticalalignment='bottom', horizontalalignment=ha,
                        fontsize=9, color='dimgray', zorder=2) # zorder=2 to draw above lines
            except Exception as e:
                 print(f"Error plotting text for event {event}: {e}") # Catch potential errors during plotting


    lines, labels = ax.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()

    # Changed bbox_to_anchor x-value from 1.03 to 1.05
    ax.legend(lines + lines2, labels + labels2, loc='upper left', bbox_to_anchor=(1.05, 1.0), borderaxespad=0.)

    ax.grid(True, linestyle=':', color='dimgray', alpha=1.0)

    plt.tight_layout()

    filename = f"static/{sanitize_object_name(object_name).replace(' ', '_')}_{selected_location.replace(' ', '_')}_altitude_plot.png"
    plt.savefig(filename)
    plt.close(fig)
    return filename


def plot_yearly_altitude_curve(
        object_name, alt_name, ra, dec, lat, lon, tz_name, selected_location, year=2025
):

    local_tz = pytz.timezone(tz_name)

    dates = []
    obj_altitudes = []
    moon_altitudes = []

    # Create an EarthLocation object once, for efficiency
    location = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
    sky_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)

    # Loop over each day of the year (Jan 1 through Dec 31)
    start_date = datetime(year, 1, 1)
    end_date = datetime(year + 1, 1, 1)  # up to but not including Jan 1 next year
    delta = timedelta(days=2) #every second day for performance reasons

    current_date = start_date
    while current_date < end_date:
        # Local midnight
        local_midnight = local_tz.localize(
            current_date.replace(hour=0, minute=0, second=0, microsecond=0)
        )
        # Convert local midnight to UTC for astropy
        midnight_utc = local_midnight.astimezone(pytz.utc)
        midnight_time_astropy = Time(midnight_utc.strftime('%Y-%m-%dT%H:%M:%S'), scale='utc')

        # Compute object altitude at local midnight
        altaz_frame = AltAz(obstime=midnight_time_astropy, location=location)
        obj_altaz = sky_coord.transform_to(altaz_frame)
        obj_alt = obj_altaz.alt.deg

        # Compute Moon altitude at local midnight
        moon_coord = get_body('moon', midnight_time_astropy, location=location)
        moon_altaz = moon_coord.transform_to(altaz_frame)
        moon_alt = moon_altaz.alt.deg

        dates.append(local_midnight.replace(tzinfo=None))  # naive for plotting
        obj_altitudes.append(obj_alt)
        moon_altitudes.append(moon_alt)

        current_date += delta

    fig, ax = plt.subplots(figsize=(11, 6))

    # Plot the object altitude
    ax.plot(dates, obj_altitudes, label=f"{object_name} Alt.", color='tab:blue', linewidth=3)

    # Plot the Moon altitude
    ax.plot(dates, moon_altitudes, label="Moon Alt.", color='gold', linestyle='-', linewidth=1.5)

    ax.set_xlabel(f"Date ({year})", fontsize=9)
    ax.set_ylabel("Altitude (°)", fontsize=9)
    ax.set_title(f"Yearly Altitude at Local Midnight for {object_name} ({alt_name}) - {selected_location}", fontsize=12, loc='left')

    # Format x-axis for months
    ax.xaxis.set_major_locator(mdates.MonthLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%b'))
    plt.xticks(rotation=45)

    # Plot horizon
    ax.axhline(y=0, color='black', linestyle='-', linewidth=2, label='Horizon', zorder=10)

    ax.set_ylim(-90, 90)
    ax.grid(True)
    ax.legend(loc='upper left', bbox_to_anchor=(1.05, 1))
    plt.tight_layout()

    # Build the filename
    from os.path import join
    filename = f"{sanitize_object_name(object_name)}_{selected_location}_yearly_alt_{year}.png"
    filepath = join("static", filename)
    plt.savefig(filepath)
    plt.close(fig)

    return filepath


def plot_monthly_altitude_curve(
        object_name, alt_name, ra, dec, lat, lon, tz_name, selected_location, year=2025, month=1
):

    local_tz = pytz.timezone(tz_name)
    # Determine the number of days in the month
    num_days = calendar.monthrange(year, month)[1]

    dates = []
    obj_altitudes = []
    moon_altitudes = []

    location = EarthLocation(lat=lat * u.deg, lon=lon * u.deg)
    sky_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)

    # Loop over every day in the month
    for day in range(1, num_days + 1):
        current_date = datetime(year, month, day)
        local_midnight = local_tz.localize(current_date.replace(hour=0, minute=0, second=0, microsecond=0))
        midnight_utc = local_midnight.astimezone(pytz.utc)
        time_astropy = Time(midnight_utc.strftime('%Y-%m-%dT%H:%M:%S'), scale='utc')

        altaz_frame = AltAz(obstime=time_astropy, location=location)
        obj_alt = sky_coord.transform_to(altaz_frame).alt.deg

        # Calculate Moon altitude
        moon_coord = get_body('moon', time_astropy, location=location)
        moon_alt = moon_coord.transform_to(altaz_frame).alt.deg

        dates.append(local_midnight.replace(tzinfo=None))  # Naive time for plotting
        obj_altitudes.append(obj_alt)
        moon_altitudes.append(moon_alt)

    # Plotting
    fig, ax = plt.subplots(figsize=(11, 6))
    ax.plot(dates, obj_altitudes, label=f"{object_name} Altitude", color='tab:blue', linewidth=3)
    ax.plot(dates, moon_altitudes, label="Moon Altitude", color='gold', linestyle='-', linewidth=1.5)

    ax.set_xlabel(f"Day of {year}-{month:02d}", fontsize=9)
    ax.set_ylabel("Altitude (°)", fontsize=9)
    ax.set_title(f"Monthly Altitude at Local Midnight for {object_name} ({alt_name}) - {selected_location}", fontsize=12, loc='left')

    # Format the x-axis to show the day of the month
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=2))
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%d'))
    plt.xticks(rotation=45)

    # Plot horizon
    ax.axhline(y=0, color='black', linestyle='-', linewidth=2, label='Horizon', zorder=10)

    ax.set_ylim(-90, 90)
    ax.grid(True)
    ax.legend(loc='upper left', bbox_to_anchor=(1.05, 1))
    plt.tight_layout()

    # Save the plot to a file
    from os.path import join
    filename = f"{sanitize_object_name(object_name)}_{selected_location}_monthly_alt_{year}_{month:02d}.png"
    filepath = join("static", filename)
    plt.savefig(filepath)
    plt.close(fig)

    return filepath

# =============================================================================
# Protected Routes
# =============================================================================

@app.route('/get_locations')
def get_locations():
    return jsonify({"locations": list(g.locations.keys()), "selected": g.selected_location})

@app.route('/search_object', methods=['POST'])
@login_required
def search_object():
    # Expect JSON input with the object identifier.
    object_name = request.json.get('object')
    if not object_name:
        return jsonify({"status": "error", "message": "No object specified."}), 400

    data = get_ra_dec(object_name)
    if data and data.get("RA (hours)") is not None:
        return jsonify({"status": "success", "data": data})
    else:
        # Return an error message from the lookup.
        return jsonify({"status": "error", "message": data.get("Common Name", "Object not found.")}), 404


def check_and_fill_object_data(config_data):
    """
    Iterates through objects in config_data, fetches missing or placeholder details
    using nova_data_fetcher, and updates the config_data dictionary in place.
    Returns True if any data was modified, False otherwise.
    """
    if not config_data or 'objects' not in config_data:
        print("[CONFIG CHECK/FETCH] No 'objects' key in config_data or config_data is empty.")
        return False

    objects_list = config_data.get('objects', [])
    if not isinstance(objects_list, list):
        print("[WARNING] 'objects' in config is not a list. Skipping auto-fill.")
        return False

    modified = False
    # Fields to check and their corresponding keys in the data returned by nova_data_fetcher
    fields_to_check = {
        # Config Key : Fetcher Key from nova_data_fetcher.get_astronomical_data()
        "Type": "object_type",
        "Magnitude": "magnitude",
        "Size": "size_arcmin",  # Assuming your YAML uses "Size" for size_arcmin
        "SB": "surface_brightness",
    }

    # Values that indicate a field should be (re-)fetched
    refetch_trigger_values = [None, "", "Not Found", "Fetch Error"]
    # Value to set if fetcher returns None for a field that was attempted
    placeholder_on_fetch_failure = "Not Found"
    # Value to set if the fetch operation itself throws an exception for an object
    placeholder_on_exception = "Fetch Error"

    print("[CONFIG CHECK/FETCH] Checking objects for missing or placeholder data...")
    objects_processed_for_fetching = 0
    objects_actually_updated = 0

    for obj_entry in objects_list:
        if not isinstance(obj_entry, dict) or "Object" not in obj_entry:
            print(f"[WARNING] Skipping invalid object entry: {obj_entry}")
            continue

        object_name = obj_entry["Object"]

        fields_that_need_update = {}
        for config_key, fetcher_key in fields_to_check.items():
            current_value = obj_entry.get(config_key)

            # MODIFIED: Condition to trigger refetch
            needs_refetch = False
            if current_value in refetch_trigger_values:
                needs_refetch = True
            elif isinstance(current_value, str):
                # Check for empty string after stripping, or case-insensitive "none"
                if current_value.strip() == "" or current_value.strip().lower() == 'none':
                    needs_refetch = True

            if needs_refetch:
                fields_that_need_update[config_key] = fetcher_key

        if fields_that_need_update:
            print(
                f"--- Attempting to fetch/update data for {object_name} for fields: {list(fields_that_need_update.keys())} ---")
            objects_processed_for_fetching += 1
            object_had_an_update_this_round = False

            try:
                fetched_data = nova_data_fetcher.get_astronomical_data(object_name)

                for config_key, fetcher_key in fields_that_need_update.items():
                    new_value_from_fetcher = fetched_data.get(fetcher_key)

                    if new_value_from_fetcher is not None and new_value_from_fetcher != "":
                        try:
                            if isinstance(new_value_from_fetcher, float):
                                if config_key == "Magnitude":
                                    new_value_formatted = round(new_value_from_fetcher, 2)
                                elif config_key == "Size":
                                    new_value_formatted = round(new_value_from_fetcher, 2)
                                elif config_key == "SB":
                                    new_value_formatted = round(new_value_from_fetcher, 2)
                                else:
                                    new_value_formatted = new_value_from_fetcher
                            else:
                                new_value_formatted = str(new_value_from_fetcher).strip()
                        except ValueError:
                            print(
                                f"    [WARN] Could not format fetched value '{new_value_from_fetcher}' for {config_key} of {object_name}. Storing as is or placeholder.")
                            new_value_formatted = placeholder_on_fetch_failure

                        current_config_value = obj_entry.get(config_key)
                        should_update = False
                        if current_config_value in refetch_trigger_values or \
                                (isinstance(current_config_value,
                                            str) and current_config_value.strip().lower() == 'none'):
                            should_update = True  # Always update if current value is a trigger
                        elif current_config_value != new_value_formatted:
                            should_update = True  # Update if different from existing valid value

                        if should_update:
                            obj_entry[config_key] = new_value_formatted
                            print(
                                f"    Updated '{config_key}' for {object_name} = {new_value_formatted} (Source: {fetched_data.get(fetcher_key.replace('_arcmin', '').replace('object_', '') + '_source', 'N/A')})")
                            modified = True
                            object_had_an_update_this_round = True
                    else:
                        if obj_entry.get(config_key) != placeholder_on_fetch_failure:
                            obj_entry[config_key] = placeholder_on_fetch_failure
                            print(
                                f"    Marked '{config_key}' as '{placeholder_on_fetch_failure}' for {object_name} (fetcher returned no data for this field).")
                            modified = True
                            object_had_an_update_this_round = True

                if object_had_an_update_this_round:
                    objects_actually_updated += 1

            except Exception as e:
                print(f"[ERROR] Fetch operation failed for {object_name}: {e}")
                for config_key in fields_that_need_update:
                    if obj_entry.get(config_key) != placeholder_on_exception:
                        obj_entry[config_key] = placeholder_on_exception
                        modified = True
                        object_had_an_update_this_round = True
                if fields_that_need_update and not object_had_an_update_this_round:
                    objects_actually_updated += 1

            time.sleep(0.5)

    if modified:
        print(
            f"[CONFIG CHECK/FETCH] Processed {objects_processed_for_fetching} objects for potential updates, {objects_actually_updated} objects had at least one field updated/marked. Config needs saving.")
    else:
        print("[CONFIG CHECK/FETCH] No objects required data fetching or re-fetching based on current criteria.")

    return modified

@app.route('/fetch_object_details', methods=['POST'])
@login_required
def fetch_object_details():
    """
    Fetch exactly Type, Magnitude, Size, SB for one object
    using nova_data_fetcher.
    """
    req = request.get_json()
    object_name = req.get("object")
    if not object_name:
        return jsonify({"status": "error", "message": "No object specified."}), 400

    try:
        fetched = nova_data_fetcher.get_astronomical_data(object_name)
        return jsonify({
            "status": "success",
            "data": {
                "Type":      fetched.get("object_type")        or "",
                "Magnitude": fetched.get("magnitude")          or "",
                "Size":      fetched.get("size_arcmin")        or "",
                "SB":        fetched.get("surface_brightness") or ""
            }
        })
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/confirm_object', methods=['POST'])
@login_required
def confirm_object():
    req = request.get_json()
    object_name = req.get('object')
    common_name = req.get('name')
    ra = req.get('ra')
    dec = req.get('dec')
    project = req.get('project', 'none') # Default 'none' from original code

    # NEW: Get Type, Magnitude, Size, SB from the request
    obj_type = req.get('type')      # Assuming the frontend sends 'type'
    magnitude = req.get('magnitude')  # Assuming the frontend sends 'magnitude'
    size = req.get('size')          # Assuming the frontend sends 'size'
    sb = req.get('sb')              # Assuming the frontend sends 'sb'


    if not object_name or not common_name: # RA/DEC also essential for a new object
        return jsonify({"status": "error", "message": "Object ID and name are required."}), 400
    if ra is None or dec is None: # RA/DEC are critical
        return jsonify({"status": "error", "message": "RA and DEC are required for the object."}), 400


    config_data = load_user_config(current_user.username)
    objects_list = config_data.setdefault('objects', [])

    existing = next((obj for obj in objects_list if obj["Object"].lower() == object_name.lower()), None)
    if existing:
        existing["Name"] = common_name
        existing["Project"] = project
        existing["RA"] = ra
        existing["DEC"] = dec
        # Update these fields if provided, otherwise keep existing or set to a default
        existing["Type"] = obj_type if obj_type is not None else existing.get("Type", "")
        existing["Magnitude"] = magnitude if magnitude is not None else existing.get("Magnitude", "")
        existing["Size"] = size if size is not None else existing.get("Size", "")
        existing["SB"] = sb if sb is not None else existing.get("SB", "")
    else:
        new_obj = {
            "Object": object_name,
            "Name": common_name,
            "Project": project,
            "RA": ra,
            "DEC": dec,
            # Add the new fields here
            "Type": obj_type if obj_type is not None else "", # Default to empty string if not provided
            "Magnitude": magnitude if magnitude is not None else "",
            "Size": size if size is not None else "",
            "SB": sb if sb is not None else ""
            # Consider if default should be "N/A" or actual None if field is truly absent
        }
        objects_list.append(new_obj)

    save_user_config(current_user.username, config_data)
    return jsonify({"status": "success"})

@app.route('/data')
def get_data():
    local_tz = pytz.timezone(g.tz_name)
    current_datetime_local = datetime.now(local_tz)
    local_date = current_datetime_local.strftime('%Y-%m-%d')

    # Define the "observing date" by subtracting 12 hours before getting the date part.
    # This correctly assigns post-midnight hours to the previous calendar date's "night".
    observing_date_for_calcs = current_datetime_local - timedelta(hours=12)
    local_date = observing_date_for_calcs.strftime('%Y-%m-%d')

    current_time_utc = current_datetime_local.astimezone(pytz.utc).strftime('%Y-%m-%dT%H:%M:%S')
    fixed_time_utc_str = get_utc_time_for_local_11pm(g.tz_name)
    altitude_threshold = g.user_config.get("altitude_threshold", 20)

    object_data_list = [] # Renamed to avoid conflict with 'data' variable from get_ra_dec
    prev_alts = session.get('previous_altitudes', {})

    # g.objects is a list of object names from the config
    for obj_name_from_config in g.objects:
        # Call the modified get_ra_dec to get all details (RA/DEC + Type, Mag, etc. from config)
        # The 'data' variable here will now hold the extended dictionary
        obj_details = get_ra_dec(obj_name_from_config)

        if not obj_details or obj_details.get("RA (hours)") is None or obj_details.get("DEC (degrees)") is None:
            # Handle cases where essential RA/DEC are missing even after get_ra_dec logic
            object_data_list.append({
                'Object': obj_name_from_config,
                'Common Name': obj_details.get("Common Name", "Error: RA/DEC lookup failed"),
                'RA (hours)': "N/A",
                'DEC (degrees)': "N/A",
                'Altitude Current': 100, # Or some other error indicator
                'Azimuth Current': "N/A",
                'Altitude 11PM': "N/A",
                'Azimuth 11PM': "N/A",
                'Transit Time': "N/A",
                'Observable Duration (min)': "N/A",
                'Trend': "N/A",
                'Project': obj_details.get('Project', "none"),
                'Time': current_datetime_local.strftime('%Y-%m-%d %H:%M:%S'),
                'Max Altitude (°)': "N/A",
                'Angular Separation (°)': "N/A",
                # Add placeholders for new fields in error case too
                'Type': obj_details.get('Type', 'N/A'),
                'Magnitude': obj_details.get('Magnitude', 'N/A'),
                'Size': obj_details.get('Size', 'N/A'),
                'SB': obj_details.get('SB', 'N/A'),
            })
            continue

        try:
            ra = obj_details["RA (hours)"] # Already float from get_ra_dec
            dec = obj_details["DEC (degrees)"] # Already float from get_ra_dec

            # --- Compute live values ---
            alt_current, az_current = ra_dec_to_alt_az(ra, dec, g.lat, g.lon, current_time_utc)

            # --- Get 11PM/transit/obs/max from static_cache ---
            # Using obj_name_from_config for cache key consistency
            cache_key = f"{obj_name_from_config.lower()}_{local_date}_{g.selected_location}"
            if cache_key in static_cache:
                cached_positional = static_cache[cache_key]
            else:
                alt_11pm, az_11pm = ra_dec_to_alt_az(ra, dec, g.lat, g.lon, fixed_time_utc_str)
                transit_time = calculate_transit_time(ra, g.lat, g.lon, g.tz_name)
                # altitude_threshold already defined above
                obs_duration, max_alt = calculate_observable_duration_vectorized(
                    ra, dec, g.lat, g.lon, local_date, g.tz_name, altitude_threshold
                )
                cached_positional = {
                    'Altitude 11PM': alt_11pm,
                    'Azimuth 11PM': az_11pm,
                    'Transit Time': transit_time,
                    'Observable Duration (min)': int(obs_duration.total_seconds() / 60) if obs_duration else 0,
                    'Max Altitude (°)': round(max_alt, 1) if max_alt is not None else "N/A"
                }
                static_cache[cache_key] = cached_positional

            # --- Moon angular separation (hourly cache) ---
            current_hour_str = current_datetime_local.strftime('%Y-%m-%d_%H')
            moon_key = f"{obj_name_from_config.lower()}_{current_hour_str}_{g.selected_location}"

            if moon_key in moon_separation_cache:
                angular_sep = moon_separation_cache[moon_key]
            else:
                location = EarthLocation(lat=g.lat * u.deg, lon=g.lon * u.deg)
                time_obj = Time(current_time_utc, format='isot', scale='utc')
                moon_coord = get_body('moon', time_obj, location=location)
                moon_altaz = moon_coord.transform_to(AltAz(obstime=time_obj, location=location))

                obj_coord_sky = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg) # Renamed to avoid conflict
                obj_altaz_sky = obj_coord_sky.transform_to(AltAz(obstime=time_obj, location=location))

                angular_sep = obj_altaz_sky.separation(moon_altaz).deg
                moon_separation_cache[moon_key] = round(angular_sep, 1)


            # --- Trend ---
            prev_alt = prev_alts.get(obj_name_from_config)
            trend = '→' # Default trend
            if prev_alt is not None: # Check if there's a previous altitude
                if alt_current > prev_alt:
                    trend = '↑'
                elif alt_current < prev_alt:
                    trend = '↓'
            prev_alts[obj_name_from_config] = alt_current


            # Append all data, including new fields from obj_details
            object_data_list.append({
                'Object': obj_details['Object'], # From get_ra_dec
                'Common Name': obj_details['Common Name'], # From get_ra_dec
                'RA (hours)': ra, # Already float
                'DEC (degrees)': dec, # Already float
                'Altitude Current': alt_current,
                'Azimuth Current': az_current,
                'Altitude 11PM': cached_positional['Altitude 11PM'],
                'Azimuth 11PM': cached_positional['Azimuth 11PM'],
                'Transit Time': cached_positional['Transit Time'],
                'Observable Duration (min)': cached_positional['Observable Duration (min)'],
                'Max Altitude (°)': cached_positional['Max Altitude (°)'],
                'Angular Separation (°)': round(angular_sep) if angular_sep is not None else "N/A",
                'Trend': trend,
                'Project': obj_details.get('Project', "none"), # From get_ra_dec
                'Time': current_datetime_local.strftime('%Y-%m-%d %H:%M:%S'),
                # NEWLY ADDED FIELDS (from obj_details, which gets them from config)
                'Type': obj_details.get('Type', 'N/A'),
                'Magnitude': obj_details.get('Magnitude', 'N/A'),
                'Size': obj_details.get('Size', 'N/A'),
                'SB': obj_details.get('SB', 'N/A'),
            })

        except Exception as e:
            print(f"[ERROR processing object {obj_name_from_config} in /data]: {e}")
            # Append with error indication if something goes wrong during processing an object
            object_data_list.append({
                'Object': obj_name_from_config,
                'Common Name': f"Error processing: {e}",
                # ... fill other fields with N/A or defaults ...
                'Type': 'N/A', 'Magnitude': 'N/A', 'Size': 'N/A', 'SB': 'N/A',
            })


    session['previous_altitudes'] = prev_alts
    # Sort by current altitude, ensuring 'Altitude Current' is float for sorting
    sorted_objects = sorted(
        object_data_list,
        key=lambda x: float(x['Altitude Current']) if isinstance(x.get('Altitude Current'), (int, float, str)) and str(x.get('Altitude Current')).replace('.', '', 1).isdigit() else -float('inf'),
        reverse=True
    )
    response = jsonify({
        "date": local_date,
        "time": current_datetime_local.strftime('%H:%M:%S'),
        "phase": round(ephem.Moon(current_datetime_local).phase, 0),
        "altitude_threshold": altitude_threshold,
        "objects": sorted_objects
    })

    cache_timeout = 60
    response.headers['Cache-Control'] = f'public, max-age={cache_timeout}'
    response.headers['Expires'] = (datetime.now(timezone.utc) + timedelta(seconds=cache_timeout)).strftime('%a, %d %b %Y %H:%M:%S GMT')

    return response

@app.route('/sun_events')
def sun_events():
    local_date = datetime.now(pytz.timezone(g.tz_name)).strftime('%Y-%m-%d')
    events = calculate_sun_events_cached(local_date,g.tz_name, g.lat, g.lon)
    events["date"] = local_date
    return jsonify(events)


@app.route('/')
def index():
    # Determine username for loading appropriate journal and config
    # This logic should align with how you manage users throughout your app.
    # Your @app.before_request already handles setting up g.user_config, g.locations etc.
    # We just need to ensure we get the correct 'username' for the journal.

    if SINGLE_USER_MODE:
        username = "default"
        # g.is_guest is likely set to False by your before_request that logs in a dummy user
    elif current_user.is_authenticated:
        username = current_user.username
    else:
        # If guests can view the index page but have no specific journal,
        # or if you have a 'journal_guest.yaml'.
        # For now, assuming a guest might get an empty journal or one named 'guest_user'.
        # Your @app.before_request loads config for 'guest_user' if not authenticated,
        # so we can try to load a journal for 'guest_user'.
        username = "guest_user"

    journal_data = load_journal(username)  # Your function to load journal YAML
    sessions = journal_data.get('sessions', [])

    # --- Add target_common_name and calculated_integration_time to each session ---
    # This assumes g.user_config is populated by your @app.before_request
    # and contains the 'objects' list.
    objects_from_config = []
    if hasattr(g, 'user_config') and g.user_config and "objects" in g.user_config:
        objects_from_config = g.user_config.get("objects", [])

    object_names_lookup = {
        obj.get("Object"): obj.get("Name", obj.get("Object"))  # Fallback to Object ID if Name is missing
        for obj in objects_from_config if obj.get("Object")  # Ensure obj has an "Object" key
    }

    for session_entry in sessions:  # Renamed to avoid conflict with flask.session
        # Ensure target_object_id exists and is a string before lookup
        target_id = session_entry.get('target_object_id')
        if isinstance(target_id, str):
            session_entry['target_common_name'] = object_names_lookup.get(target_id, target_id)
        else:
            session_entry['target_common_name'] = "N/A"  # Or some other placeholder if ID is missing/invalid

        # ----- START: New Total Integration Time Calculation Logic -----
        total_integration_seconds = 0
        has_any_integration_data = False  # Flag to see if any exposure data was found

        # 1. Add time from general/OSC fields (if they exist and are valid)
        try:
            num_subs_general_str = session_entry.get('number_of_subs_light')
            exp_time_general_str = session_entry.get('exposure_time_per_sub_sec')

            if num_subs_general_str is not None and exp_time_general_str is not None:
                num_subs_general = int(str(num_subs_general_str))  # Ensure conversion from potential string
                exp_time_general = int(str(exp_time_general_str))  # Ensure conversion
                if num_subs_general > 0 and exp_time_general > 0:  # Only count if valid positive values
                    total_integration_seconds += (num_subs_general * exp_time_general)
                    has_any_integration_data = True
        except (ValueError, TypeError):
            # If general fields are invalid or missing, just skip them
            pass

        # 2. Add time from monochrome filter fields
        mono_filters_keys = ['L', 'R', 'G', 'B', 'Ha', 'OIII', 'SII']
        for filt_key in mono_filters_keys:
            try:
                subs_val_str = session_entry.get(f'filter_{filt_key}_subs')
                exp_val_str = session_entry.get(f'filter_{filt_key}_exposure_sec')

                if subs_val_str is not None and exp_val_str is not None:
                    subs_val = int(str(subs_val_str))  # Ensure conversion
                    exp_val = int(str(exp_val_str))  # Ensure conversion
                    if subs_val > 0 and exp_val > 0:  # Only count if valid positive values
                        total_integration_seconds += (subs_val * exp_val)
                        has_any_integration_data = True
            except (ValueError, TypeError):
                # If fields for a specific filter are invalid or missing, skip that filter
                pass

        # 3. Convert total seconds to minutes or set to 'N/A'
        if has_any_integration_data:
            # Round to the nearest whole minute, or use 1 decimal if you prefer more precision
            session_entry['calculated_integration_time_minutes'] = round(total_integration_seconds / 60.0, 0)
        else:
            session_entry['calculated_integration_time_minutes'] = 'N/A'
        # ----- END: New Total Integration Time Calculation Logic -----

    # Sort sessions by date descending by default for the journal tab initial view
    try:
        sessions.sort(key=lambda s: s.get('session_date', '1900-01-01'), reverse=True)
    except Exception as e:
        print(f"Warning: Could not sort journal sessions by date in index route: {e}")

    return render_template('index.html',
                           journal_sessions=sessions
                           # Pass any other variables your index.html template already expects.
                           # For example, if your base.html or index.html uses 'is_guest':
                           # is_guest = g.is_guest if hasattr(g, 'is_guest') else (not current_user.is_authenticated and not SINGLE_USER_MODE)
                           )


@app.route('/config_form', methods=['GET', 'POST'])
@login_required
def config_form():
    error = None
    message = None
    updated = False

    if request.method == 'POST':
        try:
            if 'submit_general' in request.form:
                new_altitude_threshold = request.form.get('altitude_threshold')
                new_default_location = request.form.get('default_location', g.user_config.get("default_location", ""))

                try:
                    threshold_value = int(new_altitude_threshold)
                    if threshold_value < 0 or threshold_value > 90:
                        raise ValueError("Altitude threshold must be between 0 and 90 degrees.")
                    g.user_config['altitude_threshold'] = threshold_value
                except ValueError as ve:
                    error = str(ve)  # Capture error but continue if possible

                g.user_config[
                    'default_location'] = new_default_location if new_default_location else "Singapore"  # Default from original

                imaging = g.user_config.setdefault("imaging_criteria", {})
                try:
                    imaging["min_observable_minutes"] = int(request.form.get("min_observable_minutes", 60))
                    imaging["min_max_altitude"] = int(request.form.get("min_max_altitude", 30))
                    imaging["max_moon_illumination"] = int(request.form.get("max_moon_illumination", 20))
                    imaging["min_angular_distance"] = int(
                        request.form.get("min_angular_separation", 30))  # name from original
                    imaging["search_horizon_months"] = int(request.form.get("search_horizon_months", 6))
                except ValueError as ve:
                    error = f"Invalid imaging criteria: {ve}" if not error else error + f"; Invalid imaging criteria: {ve}"

                # Only set message and updated if no critical error has stopped us earlier
                # The original code set this regardless of 'error' status for this block.
                # To maintain similar flow, we'll set it, but errors might have occurred.
                message = "Settings updated."
                updated = True

            elif 'submit_new_location' in request.form:
                new_location_name = request.form.get("new_location")
                new_location_lat = request.form.get("new_lat")
                new_location_lon = request.form.get("new_lon")
                new_location_timezone = request.form.get("new_timezone")

                if not new_location_name or not new_location_lat or not new_location_lon or not new_location_timezone:
                    error = "All fields are required to add a new location."
                else:
                    try:
                        lat_val = float(new_location_lat)
                        lon_val = float(new_location_lon)
                        if new_location_timezone not in pytz.all_timezones:
                            raise ValueError("Invalid timezone provided.")
                        g.user_config.setdefault('locations', {})[new_location_name] = {
                            "lat": lat_val,
                            "lon": lon_val,
                            "timezone": new_location_timezone
                        }
                        message = "New location added successfully."
                        updated = True
                    except ValueError as ve:
                        error = f"Invalid input for new location: {ve}"

            elif 'submit_locations' in request.form:
                updated_locations = {}
                changed_in_locations = False
                for loc_key, loc_data in g.user_config.get("locations", {}).items():
                    if request.form.get(f"delete_loc_{loc_key}") == "on":
                        changed_in_locations = True
                        continue  # Skip deletion

                    current_loc_dict = loc_data.copy()
                    new_lat = request.form.get(f"lat_{loc_key}", loc_data.get("lat"))
                    new_lon = request.form.get(f"lon_{loc_key}", loc_data.get("lon"))
                    new_timezone = request.form.get(f"timezone_{loc_key}", loc_data.get("timezone"))

                    potential_new_data = {
                        "lat": float(new_lat) if new_lat is not None else None,  # Ensure conversion
                        "lon": float(new_lon) if new_lon is not None else None,
                        "timezone": new_timezone
                    }
                    updated_locations[loc_key] = potential_new_data
                    if loc_data != potential_new_data:  # Compare dicts
                        changed_in_locations = True

                if changed_in_locations:
                    g.user_config['locations'] = updated_locations
                    message = "Locations updated."
                    updated = True
                # else: # If no changes, don't necessarily set message/updated status

            elif 'submit_new_object' in request.form:  # Ensure this block is correctly indented
                new_object = request.form.get("new_object")
                new_obj_name = request.form.get("new_name") or ""
                new_type = request.form.get("new_type", "")  # Default to empty string as in original
                new_obj_project = request.form.get("new_project")
                if new_object:
                    g.user_config.setdefault('objects', []).append({
                        "Object": new_object,
                        "Name": new_obj_name,
                        "Project": new_obj_project if new_obj_project else "none",  # Keep original logic
                        "Type": new_type
                        # RA, DEC, Mag, Size, SB are intentionally not set here;
                        # they are fetched by 'check_and_fill_object_data' or 'fetch_all_details'
                    })
                    message = "New object added."
                    updated = True
            # Ensure this 'elif' is at the same indentation level as the one above
            elif 'submit_objects' in request.form:  # <<< THIS IS YOUR LIKELY LINE 1386
                new_objects_list = []
                original_objects_list = g.user_config.get("objects", [])
                made_changes_this_block = False

                for existing_obj_data in original_objects_list:
                    object_key = existing_obj_data.get("Object")

                    if not object_key:
                        new_objects_list.append(existing_obj_data)
                        continue

                    if request.form.get(f"delete_{object_key}") == "on":
                        made_changes_this_block = True
                        continue

                    current_obj_values = existing_obj_data.copy()
                    fields_from_form = {
                        "Name": request.form.get(f"name_{object_key}", current_obj_values.get("Name")),
                        "RA": request.form.get(f"ra_{object_key}", current_obj_values.get("RA")),
                        "DEC": request.form.get(f"dec_{object_key}", current_obj_values.get("DEC")),
                        "Type": request.form.get(f"type_{object_key}", current_obj_values.get("Type")),
                        "Project": request.form.get(f"project_{object_key}", current_obj_values.get("Project")),
                        # Magnitude, Size, SB are preserved because we start with .copy()
                        # and don't try to get them from the form here unless you add input fields for them.
                    }

                    for field_name, new_value in fields_from_form.items():
                        # Check if the value actually changed before marking
                        if current_obj_values.get(field_name) != new_value:
                            current_obj_values[field_name] = new_value
                            made_changes_this_block = True

                    new_objects_list.append(current_obj_values)

                if made_changes_this_block:
                    g.user_config['objects'] = new_objects_list
                    updated = True
                    message = "Objects updated."

            # Centralized save if any 'updated' flag was set to True in any block
            if updated and not error:  # Check for error before saving, or decide how to handle partial saves
                save_user_config(current_user.username, g.user_config)
                if message:  # If a specific message was set
                    message += " Configuration saved."
                else:  # If no specific message, just say config saved
                    message = "Configuration saved."
                # Clear the in-memory persistent cache.
                # Optionally remove the cache file so that it's rebuilt.
            elif updated and error:
                message = (
                              message if message else "") + f" Configuration partially updated but errors occurred: {error}. Please review."

        except Exception as exception_value:
            # Ensure error is set if an unexpected exception occurs
            error = str(exception_value)
            # A message might already exist from a previous step, or we can set a new one.
            message = (message if message else "") + f" An unexpected error occurred: {exception_value}"

    # Prepare for rendering template
    # Ensure error and message are passed, even if they were modified by an exception
    final_error = error if error else request.args.get("error")
    final_message = message if message else request.args.get("message")

    return render_template('config_form.html', config=g.user_config, locations=g.locations, error=final_error,
                           message=final_message)

@app.before_request
def precompute_time_arrays():
    if current_user.is_authenticated and g.tz_name:
        local_tz = pytz.timezone(g.tz_name)
        local_date = datetime.now(local_tz).strftime('%Y-%m-%d')
        g.times_local, g.times_utc = get_common_time_arrays(g.tz_name, local_date)


@app.route('/update_project', methods=['POST'])
@login_required
def update_project():
    data = request.get_json()
    object_name = data.get('object')
    new_project = data.get('project')

    if not object_name:
        return jsonify({"status": "error", "error": "Object name is missing."}), 400

    try:
        # Load the current configuration for the user.
        config = load_user_config(current_user.username)
        found = False
        # Locate the object in the objects list.
        for obj in config.get("objects", []):
            if obj.get("Object").lower() == object_name.lower():
                obj["Project"] = new_project
                found = True
                break
        if not found:
            return jsonify({"status": "error", "error": "Object not found in configuration."}), 404

        # Save the updated configuration.
        save_user_config(current_user.username, config)

        # Optionally update the persistent cache.
        key = object_name.lower()

        return jsonify({"status": "success"})
    except Exception as exception_value:
        return jsonify({"status": "error", "error": str(exception_value)}), 500

@app.before_request
def bypass_login_in_single_user():
    if SINGLE_USER_MODE and not current_user.is_authenticated:
        # Create a dummy user.
        dummy_user = User("default", "default")
        # Log in the dummy user.
        login_user(dummy_user)


@app.route('/plot_yearly_altitude/<path:object_name>')
def plot_yearly_altitude(object_name):
    # --- Determine Year for the plot from request.args ---
    current_tz_name = g.tz_name if hasattr(g, 'tz_name') and g.tz_name else 'UTC'  # For default year
    now_for_defaults = datetime.now(pytz.timezone(current_tz_name))
    try:
        year = int(request.args.get('year', now_for_defaults.year))
        # Year validation can be added if necessary
    except ValueError:
        year = now_for_defaults.year
        # Optionally flash a message or log if invalid year provided

    # --- Get Object Details ---
    object_details_for_plot = get_ra_dec(object_name)
    if not object_details_for_plot or object_details_for_plot.get('RA (hours)') is None:
        print(f"ERROR: /plot_yearly_altitude - Could not get RA/DEC for object: {object_name}")
        return jsonify({'error': f'Data for object {object_name} not found.'}), 404

    ra_hours = object_details_for_plot['RA (hours)']
    dec_degrees = object_details_for_plot['DEC (degrees)']
    alt_name_for_plot = object_details_for_plot.get("Common Name", object_name)

    # --- Determine Location details for the plot ---
    default_lat = g.lat if hasattr(g, 'lat') else 0.0
    default_lon = g.lon if hasattr(g, 'lon') else 0.0
    default_tz_name = g.tz_name if hasattr(g, 'tz_name') and g.tz_name else 'UTC'
    default_location_name = g.selected_location if hasattr(g,
                                                           'selected_location') and g.selected_location else 'Default Location'

    plot_lat_str = request.args.get('plot_lat')
    plot_lon_str = request.args.get('plot_lon')
    plot_lat = float(plot_lat_str) if plot_lat_str is not None else None
    plot_lon = float(plot_lon_str) if plot_lon_str is not None else None
    plot_tz_name = request.args.get('plot_tz', type=str)
    plot_location_display_name = request.args.get('plot_loc_name', type=str)

    final_lat = plot_lat if plot_lat is not None else default_lat
    final_lon = plot_lon if plot_lon is not None else default_lon
    final_tz_name = plot_tz_name if plot_tz_name else default_tz_name
    final_location_name = plot_location_display_name if plot_location_display_name else default_location_name

    print(f"DEBUG: /plot_yearly_altitude - Plotting for obj='{object_name}', loc='{final_location_name}', year={year}")

    # Call your actual Matplotlib plotting function for yearly view
    filepath = plot_yearly_altitude_curve(  # Your function that generates the image
        object_name=object_name,
        alt_name=alt_name_for_plot,
        ra=ra_hours,
        dec=dec_degrees,
        lat=final_lat,
        lon=final_lon,
        tz_name=final_tz_name,
        selected_location=final_location_name,  # For graph title/filename
        year=year
    )

    if os.path.exists(filepath):
        return send_file(filepath, mimetype='image/png')
    else:
        print(f"ERROR: /plot_yearly_altitude - Plot file not found for '{object_name}' at path: {filepath}")
        return jsonify({'error': f'Yearly plot image {os.path.basename(filepath)} not found.'}), 404

@app.route('/plot_monthly_altitude/<path:object_name>')
def plot_monthly_altitude(object_name):
    # --- Determine Year and Month for the plot from request.args ---
    current_tz_name = g.tz_name if hasattr(g, 'tz_name') and g.tz_name else 'UTC'
    now_for_defaults = datetime.now(pytz.timezone(current_tz_name))
    try:
        year = int(request.args.get('year', now_for_defaults.year))
        month = int(request.args.get('month', now_for_defaults.month))
        # Basic validation
        if not (1 <= month <= 12):
            month = now_for_defaults.month
        # Year validation can be added if necessary (e.g., range)
    except ValueError:
        year = now_for_defaults.year
        month = now_for_defaults.month
        # Optionally flash a message or log if invalid year/month provided

    # --- Get Object Details ---
    object_details_for_plot = get_ra_dec(object_name)
    if not object_details_for_plot or object_details_for_plot.get('RA (hours)') is None:
        print(f"ERROR: /plot_monthly_altitude - Could not get RA/DEC for object: {object_name}")
        return jsonify({'error': f'Data for object {object_name} not found.'}), 404

    ra_hours = object_details_for_plot['RA (hours)']
    dec_degrees = object_details_for_plot['DEC (degrees)']
    alt_name_for_plot = object_details_for_plot.get("Common Name", object_name)

    # --- Determine Location details for the plot ---
    default_lat = g.lat if hasattr(g, 'lat') else 0.0
    default_lon = g.lon if hasattr(g, 'lon') else 0.0
    default_tz_name = g.tz_name if hasattr(g, 'tz_name') and g.tz_name else 'UTC'
    default_location_name = g.selected_location if hasattr(g,
                                                           'selected_location') and g.selected_location else 'Default Location'

    plot_lat_str = request.args.get('plot_lat')
    plot_lon_str = request.args.get('plot_lon')
    plot_lat = float(plot_lat_str) if plot_lat_str is not None else None
    plot_lon = float(plot_lon_str) if plot_lon_str is not None else None
    plot_tz_name = request.args.get('plot_tz', type=str)
    plot_location_display_name = request.args.get('plot_loc_name', type=str)

    final_lat = plot_lat if plot_lat is not None else default_lat
    final_lon = plot_lon if plot_lon is not None else default_lon
    final_tz_name = plot_tz_name if plot_tz_name else default_tz_name
    final_location_name = plot_location_display_name if plot_location_display_name else default_location_name

    print(
        f"DEBUG: /plot_monthly_altitude - Plotting for obj='{object_name}', loc='{final_location_name}', year={year}, month={month}")

    # Call your actual Matplotlib plotting function for monthly view
    filepath = plot_monthly_altitude_curve(  # Your function that generates the image
        object_name=object_name,
        alt_name=alt_name_for_plot,
        ra=ra_hours,
        dec=dec_degrees,
        lat=final_lat,
        lon=final_lon,
        tz_name=final_tz_name,
        selected_location=final_location_name,  # For graph title/filename
        year=year,
        month=month
    )

    if os.path.exists(filepath):
        return send_file(filepath, mimetype='image/png')
    else:
        print(f"ERROR: /plot_monthly_altitude - Plot file not found for '{object_name}' at path: {filepath}")
        return jsonify({'error': f'Monthly plot image {os.path.basename(filepath)} not found.'}), 404


@app.route('/plot/<object_name>')
def plot_altitude(object_name):
    print("DEBUG: request.args =", request.args)
    data = get_ra_dec(object_name)
    if data:
        if data['RA (hours)'] is None or data['DEC (degrees)'] is None:
            return jsonify({"error": f"Graph not available: {data.get('Project', 'No data')}"}), 400
        project = data.get('Project', "none")
        alt_name = data.get("Common Name", object_name)

        now_local = datetime.now(pytz.timezone(g.tz_name))
        day_str = request.args.get('day')
        month_str = request.args.get('month')
        year_str = request.args.get('year')

        try:
            day = int(day_str) if day_str and day_str.strip() != "" else now_local.day
        except ValueError:
            day = now_local.day

        try:
            month = int(month_str) if month_str and month_str.strip() != "" else now_local.month
        except ValueError:
            month = now_local.month

        try:
            year = int(year_str) if year_str and year_str.strip() != "" else now_local.year
        except ValueError:
            year = now_local.year

        local_date = f"{year}-{month:02d}-{day:02d}"
        print("DEBUG: Plotting for date:", local_date)  # Debug print

        filepath = plot_altitude_curve(
            object_name,
            alt_name,
            data['RA (hours)'],
            data['DEC (degrees)'],
            g.lat, g.lon,
            local_date,
            g.tz_name,
            g.selected_location
        )
        if os.path.exists(filepath):
            return render_template('graph.html',
                                   object_name=object_name,
                                   project=project,
                                   filename=os.path.basename(filepath),
                                   timestamp=datetime.now().timestamp(),
                                   date=local_date,
                                   location=g.selected_location,
                                   selected_month=month,
                                   selected_year=year)
        else:
            return jsonify({'error': 'Plot not found'}), 404
    return jsonify({"error": "Object not found"}), 404

@app.route('/graph_dashboard/<path:object_name>')
def graph_dashboard(object_name):
    # --- Initialize effective context with global defaults/URL args ---
    effective_location_name = g.selected_location if hasattr(g,
                                                             'selected_location') and g.selected_location else "Unknown"
    effective_lat = g.lat if hasattr(g, 'lat') else 0.0 # Ensure float for calcs
    effective_lon = g.lon if hasattr(g, 'lon') else 0.0 # Ensure float for calcs
    effective_tz_name = g.tz_name if hasattr(g, 'tz_name') and g.tz_name else 'UTC'

    now_at_effective_location = datetime.now(pytz.timezone(effective_tz_name))

    effective_day = now_at_effective_location.day
    effective_month = now_at_effective_location.month
    effective_year = now_at_effective_location.year

    # Override with URL args if present
    if request.args.get('day'):
        try: effective_day = int(request.args.get('day'))
        except ValueError: pass
    if request.args.get('month'):
        try: effective_month = int(request.args.get('month'))
        except ValueError: pass
    if request.args.get('year'):
        try: effective_year = int(request.args.get('year'))
        except ValueError: pass

    # --- Journal Data Logic ---
    if SINGLE_USER_MODE:
        username_for_journal = "default"
    elif current_user.is_authenticated:
        username_for_journal = current_user.username
    else:
        username_for_journal = "guest_user" # Or handle as per your guest policy

    object_specific_sessions = []
    selected_session_data = None
    requested_session_id = request.args.get('session_id')

    if username_for_journal:
        journal_data = load_journal(username_for_journal)
        all_user_sessions = journal_data.get('sessions', [])

        # --- Add calculated_integration_time_minutes to EACH session ---
        for session_for_calc in all_user_sessions:

            # ----- START: New Total Integration Time Calculation Logic (for graph_dashboard) -----
            total_integration_seconds = 0
            has_any_integration_data = False

            # 1. Add time from general/OSC fields
            try:
                num_subs_general_str = session_for_calc.get('number_of_subs_light')
                exp_time_general_str = session_for_calc.get('exposure_time_per_sub_sec')

                if num_subs_general_str is not None and exp_time_general_str is not None:
                    num_subs_general = int(str(num_subs_general_str))
                    exp_time_general = int(str(exp_time_general_str))
                    if num_subs_general > 0 and exp_time_general > 0:
                        total_integration_seconds += (num_subs_general * exp_time_general)
                        has_any_integration_data = True
            except (ValueError, TypeError):
                pass

            # 2. Add time from monochrome filter fields
            mono_filters_keys = ['L', 'R', 'G', 'B', 'Ha', 'OIII', 'SII']
            for filt_key in mono_filters_keys:
                try:
                    subs_val_str = session_for_calc.get(f'filter_{filt_key}_subs')
                    exp_val_str = session_for_calc.get(f'filter_{filt_key}_exposure_sec')

                    if subs_val_str is not None and exp_val_str is not None:
                        subs_val = int(str(subs_val_str))
                        exp_val = int(str(exp_val_str))
                        if subs_val > 0 and exp_val > 0:
                            total_integration_seconds += (subs_val * exp_val)
                            has_any_integration_data = True
                except (ValueError, TypeError):
                    pass

            # 3. Convert total seconds to minutes or set to 'N/A'
            if has_any_integration_data:
                session_for_calc['calculated_integration_time_minutes'] = round(total_integration_seconds / 60.0, 0)
            else:
                session_for_calc['calculated_integration_time_minutes'] = 'N/A'
            # ----- END: New Total Integration Time Calculation Logic -----

        object_specific_sessions = [s for s in all_user_sessions if s.get('target_object_id') == object_name]
        object_specific_sessions.sort(key=lambda s: s.get('session_date', '1900-01-01'), reverse=True)

        if requested_session_id:
            selected_session_data = next(
                (s for s in object_specific_sessions if s.get('session_id') == requested_session_id), None)
            if selected_session_data:
                # 1. Override Effective Date with Session Date
                session_date_str = selected_session_data.get('session_date')
                if session_date_str:
                    try:
                        session_date_obj = datetime.strptime(session_date_str, '%Y-%m-%d')
                        effective_day = session_date_obj.day
                        effective_month = session_date_obj.month
                        effective_year = session_date_obj.year
                    except ValueError:
                        flash(f"Invalid date in session. Using current/URL date.", "warning")

                # 2. Override Effective Location with Session Location
                session_loc_name = selected_session_data.get('location_name')
                if session_loc_name:
                    all_locations_config = g.user_config.get("locations", {})
                    session_loc_details = all_locations_config.get(session_loc_name)
                    if session_loc_details:
                        effective_lat = session_loc_details.get('lat', effective_lat)
                        effective_lon = session_loc_details.get('lon', effective_lon)
                        effective_tz_name = session_loc_details.get('timezone', effective_tz_name)
                        effective_location_name = session_loc_name
                        # Update now_at_effective_location if tz changed
                        now_at_effective_location = datetime.now(pytz.timezone(effective_tz_name))
                    else:
                        flash(f"Location '{session_loc_name}' from session not in config. Using default.", "warning")
            elif requested_session_id: # session_id was in URL but no matching session found
                flash(f"Requested session ID '{requested_session_id}' not found for this object.", "info")

    # --- Finalize effective date string and dependent calculations ---
    try:
        if not (1 <= effective_month <= 12): effective_month = now_at_effective_location.month
        max_days_in_month = calendar.monthrange(effective_year, effective_month)[1]
        if not (1 <= effective_day <= max_days_in_month):
            effective_day = now_at_effective_location.day if effective_month == now_at_effective_location.month and effective_year == now_at_effective_location.year else 1
        effective_date_obj = datetime(effective_year, effective_month, effective_day)
        effective_date_str = effective_date_obj.strftime('%Y-%m-%d')
    except ValueError:
        effective_date_obj = now_at_effective_location.date()
        effective_date_str = effective_date_obj.strftime('%Y-%m-%d')
        effective_day, effective_month, effective_year = effective_date_obj.day, effective_date_obj.month, effective_date_obj.year
        flash("Invalid date components, defaulting to today.", "warning")

    effective_local_tz = pytz.timezone(effective_tz_name)
    try:
        dt_for_moon_naive = datetime(effective_year, effective_month, effective_day, 12, 0, 0)
        dt_for_moon_local = effective_local_tz.localize(dt_for_moon_naive)
        moon_phase_for_effective_date = round(ephem.Moon(dt_for_moon_local.astimezone(pytz.utc)).phase, 0)
    except Exception as e:
        print(f"Error calculating moon phase for {effective_date_str} at {effective_location_name}: {e}")
        moon_phase_for_effective_date = "N/A"

    sun_events_for_effective_date = calculate_sun_events_cached(effective_date_str, effective_tz_name, effective_lat, effective_lon)

    object_main_details = get_ra_dec(object_name)
    if not object_main_details or object_main_details.get("RA (hours)") is None:
        flash(f"Details for '{object_name}' could not be found.", "error")
        return redirect(url_for('index'))

    # --- DEBUG PRINT STATEMENTS ---
    if selected_session_data:
        print("DEBUG: graph_dashboard - selected_session_data going to template:")
        print(json.dumps(selected_session_data, indent=2))
        print(f"DEBUG: Calculated Integ. Time: {selected_session_data.get('calculated_integration_time_minutes')}, Type: {type(selected_session_data.get('calculated_integration_time_minutes'))}")
        print(f"DEBUG: Raw Subs: {selected_session_data.get('number_of_subs_light')}, Type: {type(selected_session_data.get('number_of_subs_light'))}")
        print(f"DEBUG: Raw Exp Time: {selected_session_data.get('exposure_time_per_sub_sec')}, Type: {type(selected_session_data.get('exposure_time_per_sub_sec'))}")
    # --- END OF DEBUG PRINT STATEMENTS ---

    return render_template('graph_view.html',
                           object_name=object_name,
                           alt_name=object_main_details.get("Common Name", object_name),
                           selected_day=effective_day,
                           selected_month=effective_month,
                           selected_year=effective_year,
                           selected_date_for_display=effective_date_str,
                           header_location_name=effective_location_name,
                           header_date_display=effective_date_str,
                           header_moon_phase=moon_phase_for_effective_date,
                           header_astro_dusk=sun_events_for_effective_date.get("astronomical_dusk", "N/A"),
                           header_astro_dawn=sun_events_for_effective_date.get("astronomical_dawn", "N/A"),
                           project_notes_from_config=object_main_details.get("Project", "N/A"),
                           timestamp=datetime.now(effective_local_tz).timestamp(),
                           object_specific_sessions=object_specific_sessions,
                           selected_session_data=selected_session_data,
                           current_session_id=requested_session_id if selected_session_data else None,
                           graph_location_name_param=effective_location_name,
                           graph_lat_param=effective_lat,
                           graph_lon_param=effective_lon,
                           graph_tz_name_param=effective_tz_name
                           )

@app.route('/plot_day/<path:object_name>')
def plot_day(object_name):
    # --- Determine Date for the plot from request.args ---
    # Get timezone from global context 'g', default to UTC if not available
    current_tz_name = g.tz_name if hasattr(g, 'tz_name') and g.tz_name else 'UTC'
    now_for_defaults = datetime.now(pytz.timezone(current_tz_name))

    try:
        day = int(request.args.get('day', now_for_defaults.day))
        month = int(request.args.get('month', now_for_defaults.month))
        year = int(request.args.get('year', now_for_defaults.year))

        # Basic validation for month and day
        if not (1 <= month <= 12): month = now_for_defaults.month
        # Use calendar.monthrange to get the actual number of days in the selected month and year
        max_days_in_month = calendar.monthrange(year, month)[1]
        if not (1 <= day <= max_days_in_month):
            # If current month/year, default to current day, else default to 1st day of selected month
            day = now_for_defaults.day if month == now_for_defaults.month and year == now_for_defaults.year else 1

        local_date_for_plot = f"{year}-{month:02d}-{day:02d}"
    except ValueError:  # Handles cases like non-integer input for day/month/year
        local_date_for_plot = now_for_defaults.strftime('%Y-%m-%d')
        # If date parsing fails, you might want to log this or ensure defaults are robustly set
        # For now, this ensures local_date_for_plot always has a value.

    # --- Get Object Details ---
    object_details_for_plot = get_ra_dec(object_name)  # Your existing function
    if not object_details_for_plot or object_details_for_plot.get('RA (hours)') is None:
        print(f"ERROR: /plot_day - Could not get RA/DEC for object: {object_name}")
        # Consider returning a standard "image not available" placeholder or a 404 HTTP error
        return jsonify({'error': f'Data for object {object_name} not found.'}), 404

    ra_hours = object_details_for_plot['RA (hours)']
    dec_degrees = object_details_for_plot['DEC (degrees)']
    alt_name_for_plot = object_details_for_plot.get("Common Name", object_name)

    # --- Determine Location details for the plot ---
    # These g values are set by @app.before_request based on user's general/global selection
    default_lat = g.lat if hasattr(g, 'lat') else 0.0  # Provide a float default
    default_lon = g.lon if hasattr(g, 'lon') else 0.0  # Provide a float default
    default_tz_name = g.tz_name if hasattr(g, 'tz_name') and g.tz_name else 'UTC'
    default_location_name = g.selected_location if hasattr(g,
                                                           'selected_location') and g.selected_location else 'Default Location'

    # Try to get plot-specific location parameters from the URL query string
    plot_lat_str = request.args.get('plot_lat')
    plot_lon_str = request.args.get('plot_lon')

    plot_lat = float(plot_lat_str) if plot_lat_str is not None else None
    plot_lon = float(plot_lon_str) if plot_lon_str is not None else None
    plot_tz_name = request.args.get('plot_tz', type=str)  # Will be None if not provided
    plot_location_display_name = request.args.get('plot_loc_name', type=str)  # Will be None if not provided

    # Use provided plot parameters if they exist and are valid, otherwise use defaults from g
    final_lat = plot_lat if plot_lat is not None else default_lat
    final_lon = plot_lon if plot_lon is not None else default_lon
    final_tz_name = plot_tz_name if plot_tz_name else default_tz_name  # Use default if None or empty
    final_location_name = plot_location_display_name if plot_location_display_name else default_location_name

    print(
        f"DEBUG: /plot_day - Plotting for obj='{object_name}', loc='{final_location_name}', lat={final_lat}, lon={final_lon}, tz='{final_tz_name}', date='{local_date_for_plot}'")

    # Call your actual Matplotlib plotting function
    filepath = plot_altitude_curve(  # This is your function that generates the image
        object_name=object_name,
        alt_name=alt_name_for_plot,  # Common name for the graph title
        ra=ra_hours,
        dec=dec_degrees,
        lat=final_lat,  # Pass the final determined latitude
        lon=final_lon,  # Pass the final determined longitude
        local_date=local_date_for_plot,  # Date for the plot
        tz_name=final_tz_name,  # Pass the final determined timezone name
        selected_location=final_location_name  # Pass the final location name (for graph title/filename)
    )

    if os.path.exists(filepath):
        return send_file(filepath, mimetype='image/png')
    else:
        print(
            f"ERROR: /plot_day - Plot file not found or could not be generated for '{object_name}' at path: {filepath}")
        # You could return a placeholder "error image" or a 404 HTTP error
        return jsonify({'error': f'Plot image {os.path.basename(filepath)} not found on server.'}), 404

@app.route('/get_date_info/<object_name>')
def get_date_info(object_name):
    tz = pytz.timezone(g.tz_name)
    now = datetime.now(tz)  # current time in user's local timezone

    day = int(request.args.get('day') or now.day)
    month = int(request.args.get('month') or now.month)
    year = int(request.args.get('year') or now.year)

    # Use same time-of-day as index: current hour/minute
    local_time = tz.localize(datetime(year, month, day, now.hour, now.minute))
    phase = round(ephem.Moon(local_time).phase)

    local_date_str = f"{year}-{month:02d}-{day:02d}"
    sun_events = calculate_sun_events_cached(local_date_str,g.tz_name, g.lat, g.lon)

    return jsonify({
        "date": local_date_str,
        "phase": phase,
        "astronomical_dawn": sun_events.get("astronomical_dawn", "N/A"),
        "astronomical_dusk": sun_events.get("astronomical_dusk", "N/A")
    })


@app.route('/get_imaging_opportunities/<object_name>')
def get_imaging_opportunities(object_name):
    # Load object data from config or SIMBAD.
    data = get_ra_dec(object_name)
    if not data or data.get("RA (hours)") is None or data.get("DEC (degrees)") is None:
        return jsonify({"status": "error", "message": "Object has no valid RA/DEC."}), 400

    ra = data["RA (hours)"]
    dec = data["DEC (degrees)"]
    alt_name = data.get("Common Name", object_name)

    # Get imaging criteria.
    criteria = get_imaging_criteria()
    min_obs = criteria["min_observable_minutes"]
    min_alt = criteria["min_max_altitude"]
    max_moon = criteria["max_moon_illumination"]
    min_sep = criteria["min_angular_distance"]
    months = criteria.get("search_horizon_months", 6)

    local_tz = pytz.timezone(g.tz_name)
    today = datetime.now(local_tz).date()
    end_date = today + timedelta(days=months * 30)
    dates = [today + timedelta(days=i) for i in range((end_date - today).days)]

    # Local cache for sun events so each date is calculated only once.
    sun_events_cache = {}

    final_results = []

    for d in dates:
        date_str = d.strftime('%Y-%m-%d')
        # Check cache first. If not there, compute and store.
        if date_str not in sun_events_cache:
            sun_events_cache[date_str] = calculate_sun_events_cached(date_str, g.tz_name, g.lat, g.lon)
        sun_events = sun_events_cache[date_str]

        # Use the sun events to get, for example, the dusk time.
        dusk = sun_events.get("astronomical_dusk", "20:00")

        # Calculate observable duration and maximum altitude.
        altitude_threshold = g.user_config.get("altitude_threshold", 20)
        obs_duration, max_altitude = calculate_observable_duration_vectorized(
            ra, dec, g.lat, g.lon, date_str, g.tz_name, altitude_threshold
        )
        # Apply thresholds.
        if obs_duration.total_seconds() / 60 < min_obs:
            continue
        if max_altitude < min_alt:
            continue

        # Get the moon phase.
        local_time = local_tz.localize(datetime.combine(d, datetime.now().time()))
        moon_phase = ephem.Moon(local_time.astimezone(pytz.utc)).phase
        if moon_phase > max_moon:
            continue

        # Compute angular separation at dusk.
        try:
            dusk_time_obj = datetime.strptime(dusk, "%H:%M").time()
        except Exception:
            dusk_time_obj = datetime.strptime("20:00", "%H:%M").time()
        dusk_dt = local_tz.localize(datetime.combine(d, dusk_time_obj))
        dusk_utc = dusk_dt.astimezone(pytz.utc)

        location_obj = EarthLocation(lat=g.lat * u.deg, lon=g.lon * u.deg)
        frame = AltAz(obstime=Time(dusk_utc), location=location_obj)
        obj_coord = SkyCoord(ra=ra * u.hourangle, dec=dec * u.deg)
        moon_coord = get_body('moon', Time(dusk_utc), location=location_obj)
        obj_altaz = obj_coord.transform_to(frame)
        moon_altaz = moon_coord.transform_to(frame)
        separation = obj_altaz.separation(moon_altaz).deg
        if separation < min_sep:
            continue

        # Calculate individual scores.
        MIN_ALTITUDE = 20  # degrees threshold for a "good" altitude
        score_alt = 0 if max_altitude < MIN_ALTITUDE else min((max_altitude - MIN_ALTITUDE) / (90 - MIN_ALTITUDE), 1)
        score_duration = min(obs_duration.total_seconds() / (3600 * 12), 1)  # maximum 12 hours
        score_moon_illum = 1 - min(moon_phase / 100, 1)
        score_moon_sep = min(separation / 180, 1)
        score_moon_sep_dynamic = (1 - (moon_phase / 100)) + (moon_phase / 100) * score_moon_sep

        # Composite score using equal weights (adjust weights as desired).
        composite_score = 100 * (
                0.20 * score_alt + 0.15 * score_duration + 0.45 * score_moon_illum + 0.20 * score_moon_sep_dynamic
        )
        # Map composite score to stars (1 to 5 stars).
        stars = int(round((composite_score / 100) * 4)) + 1
        star_string = "★" * stars + "☆" * (5 - stars)

        final_results.append({
            "date": date_str,
            "obs_minutes": int(obs_duration.total_seconds() / 60),
            "max_alt": round(max_altitude, 1),
            "moon_illumination": round(moon_phase, 1),
            "moon_separation": round(separation, 1),
            "rating": star_string
        })

    return jsonify({"status": "success", "object": object_name, "alt_name": alt_name, "results": final_results})

@app.route('/generate_ics/<object_name>')
def generate_ics(object_name):
    # --- 1. Get required parameters from the URL query string ---
    date_str = request.args.get('date') # e.g., "2025-08-19"
    tz_name = request.args.get('tz')
    lat = float(request.args.get('lat'))
    lon = float(request.args.get('lon'))

    # Get optional parameters for the event description
    max_alt = request.args.get('max_alt', 'N/A')
    moon_illum = request.args.get('moon_illum', 'N/A')
    obs_dur = request.args.get('obs_dur', 'N/A')

    if not all([date_str, tz_name, lat is not None, lon is not None]):
        return "Error: Missing required parameters (date, tz, lat, lon).", 400

    try:
        # --- 2. Calculate Precise Start and End Times ---
        target_date = datetime.strptime(date_str, '%Y-%m-%d')
        next_day_date = target_date + timedelta(days=1)
        next_day_date_str = next_day_date.strftime('%Y-%m-%d')

        # Get sun events for the target evening and the next morning
        sun_events_today = calculate_sun_events_cached(date_str, tz_name, lat, lon)
        sun_events_next_day = calculate_sun_events_cached(next_day_date_str, tz_name, lat, lon)

        dusk_str = sun_events_today.get("astronomical_dusk", "20:00") # Fallback time
        dawn_str = sun_events_next_day.get("astronomical_dawn", "05:00") # Fallback time

        # Create timezone-aware datetime objects for the event start (dusk) and end (dawn)
        local_tz = pytz.timezone(tz_name)
        start_time_local = local_tz.localize(datetime.combine(target_date, datetime.strptime(dusk_str, "%H:%M").time()))
        end_time_local = local_tz.localize(datetime.combine(next_day_date, datetime.strptime(dawn_str, "%H:%M").time()))

        # Convert to arrow objects for easy UTC conversion, which .ics requires
        start_time_utc = arrow.get(start_time_local)
        end_time_utc = arrow.get(end_time_local)

        # --- 3. Get Object's Common Name ---
        object_details = get_ra_dec(object_name)
        common_name = object_details.get("Common Name", object_name)

        # --- 4. Create the Calendar Event ---
        c = Calendar()
        e = Event()
        e.name = f"Imaging: {common_name}"
        e.begin = start_time_utc
        e.end = end_time_utc
        e.location = f"Lat: {lat}, Lon: {lon}"
        e.description = (
            f"Astrophotography opportunity for {common_name} ({object_name}).\n\n"
            f"Details for the night of {date_str}:\n"
            f"- Max Altitude: {max_alt}°\n"
            f"- Observable Duration: {obs_dur} min\n"
            f"- Moon Illumination: {moon_illum}%\n\n"
            f"Event times are set from Astronomical Dusk to the next Astronomical Dawn."
        )

        c.events.add(e)

        # --- 5. Return the .ics file as a response ---
        ics_content = str(c)
        filename = f"imaging_{object_name.replace(' ', '_')}_{date_str}.ics"

        return ics_content, 200, {
            'Content-Type': 'text/calendar; charset=utf-8',
            'Content-Disposition': f'attachment; filename="{filename}"'
        }

    except Exception as ex:
        print(f"ERROR generating ICS file: {ex}")
        return f"An error occurred while generating the calendar file: {ex}", 500

# =============================================================================
# Main Entry Point
# =============================================================================
if __name__ == '__main__':
    # Automatically disable debugger and reloader if set by the updater
    disable_debug = os.environ.get("NOVA_NO_DEBUG") == "1"

    app.run(
        debug=not disable_debug,
        use_reloader=not disable_debug,
        host='0.0.0.0',
        port=5001
    )

